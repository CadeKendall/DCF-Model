"""Offline protocol/security tests. All provider responses here are synthetic."""

import io
import json
import traceback
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest

from app import fmp, verify_fmp


FAKE_KEY = "synthetic-test-key-not-a-real-credential"


@pytest.fixture(autouse=True)
def no_live_requests(monkeypatch):
    opener = Mock()
    opener.open.side_effect = AssertionError("Tests must never contact FMP")
    monkeypatch.setattr(fmp, "build_opener", lambda *args: opener)
    return opener


@pytest.fixture
def configured(tmp_path, no_live_requests):
    env_path = tmp_path / ".env"
    env_path.write_text(f"FMP_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    raw_dir = tmp_path / "raw"
    client = fmp.FMPClient(env_path=env_path, raw_dir=raw_dir)
    return client, no_live_requests, raw_dir


def reply(opener, body, status=200):
    response = io.BytesIO(body)
    response.status = status
    opener.open.side_effect = None
    opener.open.return_value = response


@pytest.mark.parametrize("content", [None, "", "FMP_API_KEY=", "FMP_API_KEY='  '"])
def test_missing_key_fails_before_network(tmp_path, no_live_requests, content):
    env_path = tmp_path / ".env"
    if content is not None:
        env_path.write_text(content, encoding="utf-8")
    with pytest.raises(fmp.FMPConfigurationError, match="missing"):
        fmp.FMPClient(env_path=env_path)
    no_live_requests.open.assert_not_called()


def test_env_loading_is_explicit_and_does_not_interpolate(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text('\ufeffFMP_API_KEY="literal-${TOKEN}"\n', encoding="utf-8")
    monkeypatch.setenv("TOKEN", "should-not-be-used")
    monkeypatch.setenv("FMP_API_KEY", "should-not-override-local-file")
    monkeypatch.chdir(tmp_path.parent)
    client = fmp.FMPClient(env_path=env_path, raw_dir=tmp_path / "raw")
    assert client._api_key == "literal-${TOKEN}"
    assert "literal-${TOKEN}" not in repr(client)


@pytest.mark.parametrize(
    "method,endpoint,annual",
    [
        ("get_profile", "profile", False),
        ("get_quote", "quote", False),
        ("get_income_statement", "income-statement", True),
        ("get_balance_sheet", "balance-sheet-statement", True),
        ("get_cash_flow", "cash-flow-statement", True),
    ],
)
def test_endpoints_preserve_raw_response_and_metadata(configured, method, endpoint, annual):
    client, opener, raw_dir = configured
    raw = b'[ {"symbol":"EOG", "period":"FY", "syntheticValue":null} ]\n'
    reply(opener, raw)
    result = getattr(client, method)(" eog ")

    request = opener.open.call_args.args[0]
    url = urlsplit(request.full_url)
    query = parse_qs(url.query)
    assert url.scheme == "https"
    assert url.netloc == "financialmodelingprep.com"
    assert url.path == f"/stable/{endpoint}"
    assert query == {"symbol": ["EOG"], "apikey": [FAKE_KEY], **(
        {"period": ["annual"], "limit": ["5"]} if annual else {}
    )}
    assert opener.open.call_args.kwargs == {"timeout": 20}
    assert result.raw_path.read_bytes() == raw
    assert result.records == json.loads(raw)
    assert result.records[0]["syntheticValue"] is None
    assert "revenue" not in result.records[0]
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["provider"] == "FMP"
    assert metadata["endpoint"] == fmp.BASE_URL + "/" + endpoint
    assert metadata["parameters"]["symbol"] == "EOG"
    assert metadata["retrieved_at_utc"].endswith("+00:00")
    assert metadata["raw_file"] == result.raw_path.name
    assert all(FAKE_KEY not in p.read_text() for p in raw_dir.rglob("*.json"))


@pytest.mark.parametrize("ticker", ["", "INVALID TICKER!", "../EOG", "EOG&apikey=bad", None, "A" * 21])
def test_invalid_ticker_does_not_send_or_save(configured, ticker):
    client, opener, raw_dir = configured
    with pytest.raises(fmp.FMPInvalidTickerError):
        client.get_profile(ticker)
    opener.open.assert_not_called()
    assert not raw_dir.exists()


@pytest.mark.parametrize("limit", [0, -1, 101, 1.5, "5", True])
def test_invalid_statement_limit(configured, limit):
    client, opener, _ = configured
    with pytest.raises(fmp.FMPError, match="limit"):
        client.get_income_statement("EOG", limit=limit)
    opener.open.assert_not_called()


@pytest.mark.parametrize("body", [b"", b"  ", b"[]", b"null", b"{}"])
def test_empty_response_is_not_zero_or_success(configured, body):
    client, opener, raw_dir = configured
    reply(opener, body)
    with pytest.raises(fmp.FMPEmptyResponseError):
        client.get_profile("ZZZZZZZZZZ")
    assert not raw_dir.exists()


@pytest.mark.parametrize("body", [
    b"<html>error</html>", b"[", b"\xff\xff", b'[{"symbol":"EOG","price":NaN}]'
])
def test_malformed_json(configured, body):
    client, opener, raw_dir = configured
    reply(opener, body)
    with pytest.raises(fmp.FMPResponseError, match="JSON"):
        client.get_quote("EOG")
    assert not raw_dir.exists()


@pytest.mark.parametrize("body", [b'"error"', b"[1]", b'[{}]', b'[{"symbol":"OTHER"}]'])
def test_unexpected_shape_or_symbol(configured, body):
    client, opener, _ = configured
    reply(opener, body)
    with pytest.raises(fmp.FMPResponseError):
        client.get_profile("EOG")


@pytest.mark.parametrize("body", [
    b'{"Error Message":"Invalid API KEY"}',
    b'{"error":"limit reached"}',
    b'[{"message":"Not available"}]',
])
def test_api_error_with_http_200(configured, body):
    client, opener, raw_dir = configured
    reply(opener, body)
    with pytest.raises(fmp.FMPAPIError):
        client.get_quote("EOG")
    assert not raw_dir.exists()


@pytest.mark.parametrize("status", [301, 401, 402, 403, 404, 429, 500])
def test_http_errors_never_expose_credentials(configured, status, capsys, caplog):
    client, opener, raw_dir = configured
    opener.open.side_effect = HTTPError(
        f"https://example.invalid/?apikey={FAKE_KEY}", status, FAKE_KEY, {}, io.BytesIO()
    )
    with pytest.raises(fmp.FMPAPIError, match=str(status)) as caught:
        client.get_profile("EOG")
    displayed = "".join(traceback.format_exception(caught.value))
    captured = capsys.readouterr()
    assert FAKE_KEY not in displayed + captured.out + captured.err + caplog.text
    assert not raw_dir.exists()


@pytest.mark.parametrize("error", [
    TimeoutError(FAKE_KEY), URLError(TimeoutError(FAKE_KEY)), URLError(FAKE_KEY),
    OSError(FAKE_KEY), IncompleteRead(b"partial")
])
def test_network_errors_are_safe(configured, error):
    client, opener, raw_dir = configured
    opener.open.side_effect = error
    with pytest.raises(fmp.FMPNetworkError) as caught:
        client.get_quote("EOG")
    assert FAKE_KEY not in "".join(traceback.format_exception(caught.value))
    assert not raw_dir.exists()


@pytest.mark.parametrize("period", ["Q1", None])
def test_annual_endpoint_does_not_accept_quarterly_or_unconfirmed_records(configured, period):
    client, opener, _ = configured
    reply(opener, json.dumps([{"symbol": "EOG", "period": period}]).encode())
    with pytest.raises(fmp.FMPResponseError, match="annual"):
        client.get_income_statement("EOG")


def test_echoed_key_is_not_returned_or_saved(configured):
    client, opener, raw_dir = configured
    reply(opener, json.dumps([{"symbol": "EOG", "description": FAKE_KEY}]).encode())
    with pytest.raises(fmp.FMPResponseError, match="sensitive"):
        client.get_profile("EOG")
    assert not raw_dir.exists()


def test_redirects_are_refused():
    assert fmp._NoRedirects().redirect_request(None, None, 302, "", {}, "https://example.invalid") is None


def test_repeated_downloads_do_not_overwrite(configured):
    client, opener, _ = configured
    reply(opener, b'[{"symbol":"EOG"}]')
    first = client.get_profile("EOG")
    reply(opener, b'[{"symbol":"EOG"}]')
    second = client.get_profile("EOG")
    assert first.raw_path != second.raw_path
    assert first.raw_path.exists() and second.raw_path.exists()


def test_storage_failure_is_reported(configured):
    client, opener, raw_dir = configured
    raw_dir.write_text("a file blocks directory creation", encoding="utf-8")
    reply(opener, b'[{"symbol":"EOG"}]')
    with pytest.raises(fmp.FMPStorageError):
        client.get_profile("EOG")


def test_verification_runner_checks_all_endpoints_and_hides_key(configured, monkeypatch, capsys):
    client, opener, _ = configured

    def synthetic_response(*args, **kwargs):
        response = io.BytesIO(b'[{"symbol":"EOG","period":"FY","revenue":null}]')
        response.status = 200
        return response

    opener.open.side_effect = synthetic_response
    monkeypatch.setattr(verify_fmp, "FMPClient", lambda: client)
    assert verify_fmp.main() == 0
    output = capsys.readouterr().out
    assert "5/5 passed" in output
    assert "PASS invalid ticker" in output
    assert '"revenue": null' in output
    assert "unavailable (field absent)" in output
    assert FAKE_KEY not in output
    assert opener.open.call_count == 5


def test_verification_runner_reports_partial_failure(configured, monkeypatch, capsys):
    client, opener, _ = configured
    opener.open.side_effect = URLError(FAKE_KEY)
    monkeypatch.setattr(verify_fmp, "FMPClient", lambda: client)
    assert verify_fmp.main() == 1
    output = capsys.readouterr().out
    assert "0/5 passed" in output
    assert FAKE_KEY not in output
    assert opener.open.call_count == 5


def test_verification_runner_reports_missing_configuration(monkeypatch, capsys):
    def missing_client():
        raise fmp.FMPConfigurationError("FMP_API_KEY is missing.")

    monkeypatch.setattr(verify_fmp, "FMPClient", missing_client)
    assert verify_fmp.main() == 1
    assert "FMP_API_KEY is missing" in capsys.readouterr().out


def test_explicit_annual_limit(configured):
    client, opener, _ = configured
    reply(opener, b'[{"symbol":"EOG","period":"FY"}]')
    client.get_income_statement("EOG", limit=1)
    query = parse_qs(urlsplit(opener.open.call_args.args[0].full_url).query)
    assert query["limit"] == ["1"]
    assert query["period"] == ["annual"]
