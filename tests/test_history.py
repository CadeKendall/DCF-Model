"""Synthetic financial cases; no network, credentials, or real-company forecasts."""

import json
import io
from datetime import date

import pandas as pd
import pytest

from app.history import HistoryError, load_history, load_operating_observations, normalize_sec
from app.sec import SECError, load_companyfacts


def wrds_record(year, **extra):
    return dict(gvkey="TEST", tic="TEST", fyear=year, datadate=f"{year}-12-31",
                curcd="USD", revt=100, oiadp=20, dp=5, che=4, dlc=2, dltt=8,
                rect=10 + year - 2020, invt=3, ap=5, **extra)


def save_wrds(root, records, stamp="2030-01-01T00:00:00+00:00", folder="one"):
    path = root / "data/raw/wrds" / folder / "verification.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(dict(test_ticker="TEST", retrieved_at_utc=stamp,
        compustat=dict(status="available", table="comp.funda", records=records))))


def test_window_rolls_from_latest_data_and_uses_extra_year(tmp_path):
    save_wrds(tmp_path, [wrds_record(y) for y in range(2021, 2027)])
    result = load_history("TEST", root=tmp_path, as_of=date(2030, 1, 1))
    assert list(result.annual.index) == [2022, 2023, 2024, 2025, 2026]
    assert result.annual.loc[2022, "change_nwc_proxy"] == 1
    assert result.annual.loc[2026, "ebitda"] == 25
    assert result.annual.loc[2026, "net_debt"] == 6
    assert set(result.audit.year) == set(range(2021, 2027))


def test_missing_year_is_not_skipped_in_changes(tmp_path):
    save_wrds(tmp_path, [wrds_record(2022), wrds_record(2024)])
    annual = load_history("TEST", years=3, root=tmp_path, as_of=date(2030, 1, 1)).annual
    assert pd.isna(annual.loc[2023, "revenue"])
    assert pd.isna(annual.loc[2024, "change_nwc_proxy"])
    assert pd.isna(annual.loc[2024, "revenue_growth_pct"])


def test_missing_payables_and_zero_denominator_stay_missing(tmp_path):
    row = wrds_record(2024)
    row.update(ap=None, revt=0)
    save_wrds(tmp_path, [row])
    annual = load_history("TEST", years=1, root=tmp_path, as_of=date(2030, 1, 1)).annual
    assert pd.isna(annual.loc[2024, "nwc_proxy"])
    assert pd.isna(annual.loc[2024, "ebit_margin_pct"])


def test_explicit_end_year_and_cache_freshness(tmp_path):
    save_wrds(tmp_path, [wrds_record(2021), wrds_record(2022)])
    save_wrds(tmp_path, [wrds_record(2023), wrds_record(2024)],
              stamp="2030-02-01T00:00:00+00:00", folder="two")
    result = load_history("TEST", years=2, end_year=2023, root=tmp_path, as_of=date(2030, 1, 1))
    assert list(result.annual.index) == [2022, 2023]
    assert pd.isna(result.annual.loc[2022, "revenue"])  # No silent merging older extracts.


def test_identity_and_duplicate_annual_rows_rejected(tmp_path):
    save_wrds(tmp_path, [wrds_record(2024), wrds_record(2024)])
    with pytest.raises(HistoryError, match="Duplicate"):
        load_history("TEST", root=tmp_path, as_of=date(2030, 1, 1))


def sec_fact(value, end="2024-12-31", start="2024-01-01", **extra):
    row = dict(val=value, end=end, filed="2025-02-01", form="10-K",
               accn="0000000001-25-000001", fy=2025)
    if start:
        row["start"] = start
    row.update(extra)
    return row


def sec_payload(facts):
    return dict(retrieved_at_utc="2025-03-01T00:00:00+00:00",
                submissions=dict(cik=1, tickers=["TEST"]),
                companyfacts=dict(cik=1, facts={"us-gaap": facts}))


def test_sec_actual_period_not_filing_fy_quarter_or_shares_scale():
    payload = sec_payload({
        "NetIncomeLoss": {"units": {"USD": [sec_fact(2_000_000)]}},
        "Revenues": {"units": {"USD": [sec_fact(10_000_000),
            sec_fact(999, start="2024-10-01"),
            sec_fact(999, filed="2027-01-01")] }},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {
            "units": {"shares": [sec_fact(3_000_000)]}},
    })
    data = pd.DataFrame(normalize_sec(payload, date(2025, 3, 1))).set_index("metric")
    assert data.loc["revenue", "value"] == 10
    assert data.loc["revenue", "year"] == 2024
    assert data.loc["diluted_shares", "value"] == 3
    assert pd.isna(data.loc["receivables", "value"])


def test_sec_latest_comparative_and_conflicting_facts():
    payload = sec_payload({"NetIncomeLoss": {"units": {"USD": [sec_fact(2)]}},
        "Revenues": {"units": {"USD": [sec_fact(10_000_000),
            sec_fact(11_000_000, filed="2026-02-01", accn="0000000001-26-000001")]}}})
    rows = normalize_sec(payload, date(2026, 3, 1))
    assert next(r for r in rows if r["metric"] == "revenue")["value"] == 11
    payload["companyfacts"]["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(
        sec_fact(12_000_000, filed="2026-02-01", accn="0000000001-26-000001"))
    with pytest.raises(HistoryError, match="Ambiguous"):
        normalize_sec(payload, date(2026, 3, 1))


def test_sec_no_network_when_contact_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setattr("app.sec.urlopen", lambda *a, **k: pytest.fail("Unexpected network"))
    with pytest.raises(SECError, match="SEC_USER_AGENT"):
        load_companyfacts(1, "TEST", refresh=True, root=tmp_path)


def test_operating_data_join_by_year_and_never_roll_forward(tmp_path):
    path = tmp_path / "observations.json"
    path.write_text(json.dumps({"observations": [
        dict(ticker="TEST", year=2023, metric="annual_mmboe", value=100),
        dict(ticker="TEST", year=2024, metric="annual_mmboe", value=110),
    ]}))
    values, _ = load_operating_observations(path, [2024, 2025], "TEST")
    assert values.loc[2024, "annual_mmboe"] == 110
    assert pd.isna(values.loc[2025, "annual_mmboe"])


def test_sec_download_cache_and_identity_validation(tmp_path, monkeypatch):
    # Synthetic requests verify cache behavior; no actual email or network used.
    monkeypatch.setenv("SEC_USER_AGENT", "Synthetic-Test contact@example.test")
    concepts = {"NetIncomeLoss": {"units": {"USD": [sec_fact(2_000_000)]}}}
    payload = sec_payload(concepts)
    replies = iter([payload["submissions"], payload["companyfacts"]])
    calls = []
    def fake_open(request, timeout):
        calls.append(request)
        return io.StringIO(json.dumps(next(replies)))
    monkeypatch.setattr("app.sec.urlopen", fake_open)
    monkeypatch.setattr("app.sec.time.sleep", lambda seconds: None)
    first, path = load_companyfacts(1, "TEST", refresh=True, root=tmp_path)
    assert path.is_file() and len(calls) == 2
    second, _ = load_companyfacts(1, "TEST", refresh=False, root=tmp_path)
    assert first == second and len(calls) == 2
    with pytest.raises(SECError, match="Ticker"):
        load_companyfacts(1, "DIFFERENT", root=tmp_path)


def test_sec_capex_components_require_both_values(tmp_path):
    facts = {
        "NetIncomeLoss": {"units": {"USD": [sec_fact(2_000_000)]}},
        "PaymentsToAcquireOilAndGasProperty": {"units": {"USD": [sec_fact(3_000_000)]}},
        "PaymentsToAcquireOtherPropertyPlantAndEquipment": {"units": {"USD": [sec_fact(1_000_000)]}},
    }
    payload = sec_payload(facts)
    path = tmp_path / "data/raw/sec/0000000001/one/companyfacts.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload))
    result = load_history("TEST", 1, provider="sec", cik=1, root=tmp_path, as_of=date(2025, 3, 1))
    assert result.annual.loc[2024, "capex"] == 4
    del facts["PaymentsToAcquireOtherPropertyPlantAndEquipment"]
    path.write_text(json.dumps(payload))
    result = load_history("TEST", 1, provider="sec", cik=1, root=tmp_path, as_of=date(2025, 3, 1))
    assert pd.isna(result.annual.loc[2024, "capex"])
