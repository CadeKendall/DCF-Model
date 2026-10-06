"""Synthetic offline WRDS tests; never contact WRDS or use real credentials."""

import json
import sys
import traceback
from datetime import date
from decimal import Decimal
from unittest.mock import Mock

import pytest

pytest.importorskip("wrds", reason="Run WRDS tests with .venv-wrds (Python 3.12).")
import pandas as pd

from app import verify_wrds, wrds_client as module


@pytest.fixture(autouse=True)
def no_live_auth(monkeypatch):
    monkeypatch.setattr(module.wrds, "Connection", Mock(side_effect=AssertionError("No live WRDS in tests")))


@pytest.fixture
def account():
    client = module.WRDSClient()
    db = Mock()
    client._connection = db
    db.list_libraries.return_value = ["comp", "crsp", "wrdssec_all", "other"]
    db.list_tables.side_effect = lambda library: {"comp": ["company", "funda"], "crsp": ["msf"], "wrdssec_all": ["dforms"]}[library]
    names = ["gvkey", "tic", "conm", "datadate", "fyear", "curcd", *module.FILTERS, "sale", "dltt", "dlc", "capx", "csho"]
    db.describe_table.return_value = pd.DataFrame([
        {"name": name, "nullable": True, "type": "synthetic_type", "comment": "synthetic test metadata"}
        for name in names
    ])
    identity = pd.DataFrame([{"gvkey": "TEST001", "tic": "EOG", "conm": "Synthetic company fixture"}])
    annual = pd.DataFrame([{
        "gvkey": "TEST001", "tic": "EOG", "conm": "Synthetic company fixture",
        "datadate": date(2020, 12, 31), "fyear": 2020, "curcd": "USD", **module.FILTERS,
        "sale": Decimal("123.4567890123456789"), "dltt": None, "dlc": Decimal("0"),
        "capx": Decimal("4.5"), "csho": Decimal("2"),
    }])
    db.raw_sql.side_effect = [pd.DataFrame(columns=["gvkey"]), identity, annual]
    return client, db


def test_library_and_sec_discovery_only_lists_metadata(account):
    client, db = account
    inventory = client.inspect_account()
    assert inventory["available_libraries"] == ["comp", "crsp", "other", "wrdssec_all"]
    assert inventory["sec_related_schema_candidates"] == ["wrdssec_all"]
    assert inventory["tables_by_library"]["wrdssec_all"] == ["dforms"]
    assert inventory["catalog_errors"] == {}
    db.raw_sql.assert_not_called()


def test_eog_raw_values_fields_and_parameterized_query(account, tmp_path):
    client, db = account
    inventory = client.inspect_account()
    sample = client.retrieve_eog(inventory)
    assert sample["status"] == "available"
    assert sample["table"] == "comp.funda"
    assert "oancf" in sample["unavailable_fields"]
    assert "oancf" not in sample["records"][0]
    assert sample["records"][0]["dltt"] is None
    assert sample["records"][0]["dlc"] == 0
    query = db.raw_sql.call_args.args[0]
    parameters = db.raw_sql.call_args.kwargs["params"]
    assert "TEST001" not in query and "EOG" not in query
    assert parameters == {"gvkey": "TEST001", "limit": 5, **module.FILTERS}
    assert db.raw_sql.call_args.kwargs["coerce_float"] is False
    assert "ORDER BY datadate DESC" in query
    path = module.save_report(inventory, sample, tmp_path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["compustat"]["records"][0]["sale"] == "123.4567890123456789"
    assert saved["compustat"]["records"][0]["datadate"] == "2020-12-31"
    assert saved["compustat"]["records"][0]["dltt"] is None


def test_no_compustat_retains_alternative_libraries(account):
    client, db = account
    db.list_libraries.return_value = ["crsp", "wrdssec_all"]
    inventory = client.inspect_account()
    sample = client.retrieve_eog(inventory)
    assert sample["status"] == "not_available_in_account_catalogue"
    assert inventory["relevant_libraries"] == ["crsp", "wrdssec_all"]
    db.raw_sql.assert_not_called()


def test_catalogue_failure_does_not_claim_no_subscription(account):
    client, db = account
    db.list_tables.side_effect = RuntimeError("synthetic-password diagnostic")
    inventory = client.inspect_account()
    sample = client.retrieve_eog(inventory)
    assert sample["status"] == "undetermined"
    assert "synthetic-password" not in json.dumps(inventory)


def test_sample_and_global_schemas_do_not_establish_full_na_access(account):
    client, db = account
    db.list_libraries.return_value = ["comp_global", "comp_na_daily_all_samp"]
    db.list_tables.side_effect = lambda library: ["funda"]
    assert client.retrieve_eog(client.inspect_account())["status"] == "not_available_in_account_catalogue"
    db.raw_sql.assert_not_called()


def test_denied_table_is_not_reported_available(account):
    client, db = account
    db.raw_sql.side_effect = PermissionError("synthetic-password")
    sample = client.retrieve_eog(client.inspect_account())
    assert sample["status"] == "not_available_in_account_catalogue"
    assert sample["attempts"][0]["status"] == "access_denied"


def test_empty_eog_is_different_from_no_compustat(account):
    client, db = account
    db.raw_sql.side_effect = [pd.DataFrame(), pd.DataFrame(columns=["gvkey", "tic"])]
    sample = client.retrieve_eog(client.inspect_account())
    assert sample["status"] == "available_no_eog"
    assert sample["records"] == []


def test_ambiguous_identity_stops_instead_of_picking_company(account):
    client, db = account
    db.raw_sql.side_effect = [pd.DataFrame(), pd.DataFrame({"gvkey": ["ONE", "TWO"], "tic": ["EOG", "EOG"]})]
    sample = client.retrieve_eog(client.inspect_account())
    assert sample["status"] == "undetermined"
    assert "identity" in sample["attempts"][0]["detail"]
    assert db.raw_sql.call_count == 2


def test_company_lookup_fallback_when_funda_lacks_ticker(account):
    client, db = account
    annual_description = db.describe_table.return_value
    db.describe_table.side_effect = [annual_description[annual_description.name != "tic"], pd.DataFrame({"name": ["gvkey", "tic", "conm"]})]
    sample = client.retrieve_eog(client.inspect_account())
    assert sample["status"] == "available"
    assert '"comp"."company"' in db.raw_sql.call_args_list[1].args[0]


@pytest.mark.parametrize("limit", [0, 11, True, "5"])
def test_invalid_limit_stops_before_query(account, limit):
    client, db = account
    with pytest.raises(module.WRDSError, match="limit"):
        client.retrieve_eog({}, limit=limit)
    db.raw_sql.assert_not_called()


def test_identifier_injection_rejected():
    with pytest.raises(module.WRDSError):
        module._identifier('comp; DROP TABLE x')


def test_query_error_never_exposes_driver_credentials(account, capsys):
    client, db = account
    def fail(*args, **kwargs):
        print("synthetic-password")
        raise RuntimeError("postgresql://synthetic-user:synthetic-password@host")
    db.raw_sql.side_effect = fail
    with pytest.raises(module.WRDSError) as caught:
        client._query("SELECT 1")
    assert "synthetic-password" not in "".join(traceback.format_exception(caught.value))
    assert "synthetic-password" not in capsys.readouterr().out


def test_noninteractive_signin_does_not_prompt_or_connect(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(module.WRDSError, match="PowerShell"):
        module.WRDSClient().connect()
    module.wrds.Connection.assert_not_called()


def test_standard_wrds_auth_uses_read_only_connection(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    connection = Mock()
    constructor = Mock(return_value=connection)
    monkeypatch.setattr(module.wrds, "Connection", constructor)
    client = module.WRDSClient()
    client.connect()
    options = constructor.call_args.kwargs
    assert options["verbose"] is False
    assert "wrds_username" not in options and "wrds_password" not in options
    assert "default_transaction_read_only=on" in options["wrds_connect_args"]["options"]
    assert options["wrds_connect_args"]["sslmode"] == "require"
    client.close()
    connection.close.assert_called_once()


def test_auth_failure_message_hides_driver_details(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(module.wrds, "Connection", Mock(side_effect=RuntimeError("synthetic-password")))
    with pytest.raises(module.WRDSError) as caught:
        module.WRDSClient().connect()
    assert "synthetic-password" not in "".join(traceback.format_exception(caught.value))


def test_runner_writes_report_and_closes(account, monkeypatch, tmp_path, capsys):
    client, db = account
    monkeypatch.setattr(client, "connect", lambda: None)
    monkeypatch.setattr(verify_wrds, "WRDSClient", lambda: client)
    real_save = module.save_report
    monkeypatch.setattr(verify_wrds, "save_report", lambda inventory, sample: real_save(inventory, sample, tmp_path))
    assert verify_wrds.main() == 0
    output = capsys.readouterr().out
    assert "comp.funda" in output and "EOG records: 1" in output
    assert len(list(tmp_path.glob("*/verification.json"))) == 1
    db.close.assert_called_once()
