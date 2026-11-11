"""Forward-source adapters: supplemental parser, guidance checks, STEO parser, filing cache."""

import hashlib
import io
import json

import openpyxl
import pandas as pd
import pytest

from app.eia import annual_prices, parse_steo, quarter_prices
from app.eog_quarterly import document_text, parse_supplemental
from app.guidance import GuidanceError, validate_records, verify_records
from app.sec import SECError
from app import sec_filings


# ---------- supplemental tables ----------

def table(title, header, rows):
    cells = "".join(f"<td>{h}</td>" for h in header)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<p>{title}</p><table><tr>{cells}</tr>{body}</table><p>10</p>"


def supplemental_html(q3_oil_sales=None):
    qh = ["2029", "2030", "1st Qtr", "2nd Qtr", "3rd Qtr", "4th Qtr", "Year", "1st Qtr", "2nd Qtr", "3rd Qtr", "4th Qtr", "Year"]
    nine = lambda a: [a, a, a, a, str(4 * int(a)), a, a, a, str(3 * int(a))]
    income_labels = ["Crude Oil and Condensate", "Natural Gas Liquids", "Natural Gas",
                     "Gains (Losses) on Mark-to-Market Financial Commodity and Other Derivative Contracts, Net",
                     "Gathering, Processing and Marketing", "Gains (Losses) on Asset Dispositions, Net", "Other, Net"]
    rows = [["Operating Revenues and Other"]]
    for label in income_labels:
        values = nine("10")
        if label == "Crude Oil and Condensate" and q3_oil_sales:
            values[7] = q3_oil_sales
        rows.append([label] + values)
    rows.append(["Total"] + nine("70"))
    rows.append(["Operating Expenses"])
    for label in ["Lease and Well", "Gathering, Processing and Transportation Costs", "Exploration Costs", "Dry Hole Costs",
                  "Impairments", "Marketing Costs", "Depreciation, Depletion and Amortization", "General and Administrative",
                  "Taxes Other Than Income"]:
        rows.append([label] + nine("5"))
    rows += [["Total"] + nine("45"), ["Operating Income"] + nine("25"), ["Interest Expense, Net"] + nine("1"),
             ["Income Before Income Taxes"] + nine("24"), ["Income Tax Provision"] + nine("5"),
             ["Net Income"] + nine("19"), ["Average Number of Common Shares"], ["Basic"] + ["100"] * 9,
             ["Diluted"] + ["102"] * 9]
    html = table("Income Statements", ["In millions of USD"] + qh, rows)
    vol_rows = []
    for anchor, label in [("Crude Oil and Condensate Volumes (MBod)", "Total"), ("Average Crude Oil and Condensate Prices", "Composite"),
                          ("Natural Gas Liquids Volumes (MBbld)", "Total"), ("Average Natural Gas Liquids Prices", "Composite"),
                          ("Natural Gas Volumes (MMcfd)", "United States"), ("Natural Gas Volumes (MMcfd)", "Total"),
                          ("Average Natural Gas Prices", "United States"), ("Average Natural Gas Prices", "Composite"),
                          ("Crude Oil Equivalent Volumes (MBoed)", "Total")]:
        if anchor not in [r[0] for r in vol_rows]:
            vol_rows.append([anchor])
        vol_rows.append([label] + ["$", "1.5"] * 9)
    html += table("Volumes and Prices", ["(Unaudited)"] + qh, vol_rows)
    bh = ["2029", "2030", "MAR", "JUN", "SEP", "DEC", "MAR", "JUN", "SEP", "DEC"]
    brows = [[label] + ["7"] * 7 for label in ["Cash and Cash Equivalents", "Accounts Receivable, Net", "Inventories",
                                                 "Accounts Payable", "Accrued Taxes Payable", "Dividends Payable",
                                                 "Current Portion of Long-Term Debt", "Current Portion of Operating Lease Liabilities",
                                                 "Long-Term Debt", "Total Stockholders' Equity"]]
    html += table("Balance Sheets", ["In millions of USD (Unaudited)"] + bh, brows)
    crows = [[label] + nine("3") for label in ["Stock-Based Compensation Expenses", "Deferred Income Taxes",
                                               "Net Cash Provided by Operating Activities", "Additions to Oil and Gas Properties",
                                               "Additions to Other Property, Plant and Equipment", "Proceeds from Sales of Assets"]]
    html += table("Cash Flow Statements", ["In millions of USD (Unaudited)"] + qh, crows)
    html += "<p>Non-GAAP Financial Measures</p><p>See below reconciliation of Total Expenditures (GAAP)</p>"
    html += table("x", qh, [["Asset Retirement Costs"] + ["(" + v + ")" for v in nine("2")], ["Total Capital Expenditures (Non-GAAP)"] + nine("4")])
    html += "<p>Cash Flow from Operations and Free Cash Flow</p>"
    return html.encode("utf-8")


def test_supplemental_parse_and_quarter_checks():
    flows, balances, audit, checks = parse_supplemental(supplemental_html())
    assert list(flows.index) == ["2029Q1", "2029Q2", "2029Q3", "2029Q4", "2029FY", "2030Q1", "2030Q2", "2030Q3", "2030YTD"]
    assert flows.at["2030YTD", "oil_sales"] == 30
    assert flows.at["2030Q3", "diluted_shares"] == 102  # page number "10" not read as a value
    assert flows.at["2030Q1", "realized_oil"] == 1.5  # "$" tokens skipped
    assert flows.at["2030Q1", "asset_retirement_costs"] == 2
    assert list(balances.index)[-1] == "2030-09-30"
    assert set(checks.Status) == {"Ties within rounding"}


def test_supplemental_quarter_mismatch_is_flagged():
    _, _, _, checks = parse_supplemental(supplemental_html(q3_oil_sales="40"))
    flagged = checks[(checks.metric == "oil_sales") & (checks.period == "2030YTD")]
    assert flagged.Status.iloc[0] == "Review difference"


def test_missing_row_raises():
    with pytest.raises(SECError):
        parse_supplemental(supplemental_html().replace(b"Dry Hole Costs", b"Something Else"))


# ---------- guidance ----------

SOURCES = {"img": {"accession": "0000000000-30-000001", "document": "p.jpg", "filed": "2030-11-01",
                   "url": "u", "kind": "image", "sha256": hashlib.sha256(b"\xff\xd8image").hexdigest()},
           "txt": {"accession": "0000000000-30-000002", "document": "q.htm", "filed": "2030-11-01", "url": "u", "kind": "text"}}


def record(**changes):
    base = {"id": "a", "metric": "m", "period": "2030FY", "low": 1.0, "high": 3.0, "point": 2.0,
            "units": "u", "basis": "b", "source": "img"}
    base.update(changes)
    return base


def fetch(source):
    return (b"\xff\xd8image" if source["kind"] == "image" else b"<p>Capex is expected to be  $5 billion</p>"), {}


def test_guidance_verification_passes():
    frame = validate_records({"sources": SOURCES, "records": [
        record(), record(id="b", source="txt", low=None, high=None, point=5.0, quoted_text="expected to be $5 billion")]})
    checked = verify_records(frame, SOURCES, fetch)
    assert list(checked.verification) == ["Image hash verified; values manually transcribed", "Quoted text found in filing"]


def test_guidance_quote_and_hash_failures():
    bad_quote = validate_records({"sources": SOURCES, "records": [record(source="txt", quoted_text="$6 billion")]})
    with pytest.raises(GuidanceError):
        verify_records(bad_quote, SOURCES, fetch)
    other = dict(SOURCES, img=dict(SOURCES["img"], sha256="0" * 64))
    with pytest.raises(GuidanceError):
        verify_records(validate_records({"sources": other, "records": [record()]}), other, fetch)


@pytest.mark.parametrize("changes", [dict(low=4.0), dict(point=9.0), dict(point=float("nan")), dict(source="none"),
                                     dict(low=None, high=None, point=None)])
def test_guidance_schema_errors(changes):
    with pytest.raises(GuidanceError):
        validate_records({"sources": SOURCES, "records": [record(**changes)]})


def test_document_text_normalizes_cells():
    assert document_text(b"<tr><td>A</td><td>1,234</td></tr>") == "A | 1,234 |"


# ---------- STEO ----------

def steo_workbook(months=36):
    wb = openpyxl.Workbook()
    dates = wb.active
    dates.title = "Dates"
    dates.append(["Forecast Month -", None, None, "November 2030"])
    dates.append(["Table Beginning Month--- ", None, None, 202901])
    dates.append(["Last Historical Month--- ", None, None, 203010])
    t2 = wb.create_sheet("2tab")
    t2.append(["WTIPUUS", "WTI"] + [float(60 + i // 12 * 10) for i in range(months)])
    t2.append(["NGHHMCF", "Henry Hub"] + [3.0] * months)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_steo_parse_annual_and_quarter():
    monthly, info = parse_steo(steo_workbook())
    assert info["last_historical_month"] == "2030-10"
    annual = annual_prices(monthly)
    assert list(annual.index) == [2029, 2030, 2031]
    assert annual.at[2030, "wti"] == 70 and annual.at[2030, "forecast_months"] == 2
    assert annual.at[2031, "status"] == "EIA forecast"
    assert quarter_prices(monthly, 2030, 4)["wti"] == 70


def test_steo_partial_year_excluded():
    assert list(annual_prices(parse_steo(steo_workbook(months=30))[0]).index) == [2029, 2030]


# ---------- filing cache ----------

def test_cached_document_identity_checked(tmp_path):
    cache = tmp_path / "c"
    directory = cache / "20300101T000000000000Z"
    directory.mkdir(parents=True)
    (directory / "filing.html").write_bytes(b"<p>x</p>")
    expected = {"accession": "a", "url": "u"}
    (directory / "metadata.json").write_text(json.dumps({**expected, "retrieved_at_utc": "t"}))
    content, metadata, _ = sec_filings._cached_or_download(cache, expected, refresh=False, root=tmp_path)
    assert content == b"<p>x</p>"
    with pytest.raises(SECError):
        sec_filings._cached_or_download(cache, {**expected, "accession": "b"}, refresh=False, root=tmp_path)
    with pytest.raises(SECError, match="not cached"):
        sec_filings._cached_or_download(tmp_path / "empty", expected, refresh=False, root=tmp_path)
