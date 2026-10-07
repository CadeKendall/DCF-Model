"""Year-indexed historical normalization, independent of notebook and valuation."""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from app.sec import load_companyfacts

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Mappings describe provider fields; they contain no company financial values.
WRDS_FIELDS = {
    "revenue": "revt", "ebit": "oiadp", "dd_a": "dp", "net_income": "ni",
    "operating_cash_flow": "oancf", "capex": "capx", "cash": "che",
    "diluted_shares": "cshfd", "receivables": "rect", "inventory": "invt",
    "accounts_payable": "ap", "debt_current": "dlc", "debt_noncurrent": "dltt",
    "pretax_income": "pi", "income_tax": "txt", "interest_expense": "xint",
    "operating_expenses_before_da": "xopr", "assets": "at", "liabilities": "lt",
    "equity": "seq", "ppe": "ppent", "acquisitions": "aqc", "asset_sales": "sppe",
}

# Exact standard concepts only. Custom concepts are reported as missing rather
# than guessed from a superficially similar revenue, debt, or cash-flow tag.
# Each entry is (candidate concepts, instant/duration, source unit).
SEC_FIELDS = {
    "revenue": (("Revenues",), "duration", "USD"),
    "operating_expenses": (("OperatingExpenses",), "duration", "USD"),
    "ebit": (("OperatingIncomeLoss",), "duration", "USD"),
    "dd_a": (("DepreciationDepletionAndAmortization",), "duration", "USD"),
    "net_income": (("NetIncomeLoss",), "duration", "USD"),
    "pretax_income": (("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",), "duration", "USD"),
    "income_tax": (("IncomeTaxExpenseBenefit",), "duration", "USD"),
    "interest_expense": (("InterestExpenseNonOperating",), "duration", "USD"),
    "operating_cash_flow": (("NetCashProvidedByUsedInOperatingActivities",), "duration", "USD"),
    "capex": (("PaymentsToAcquirePropertyPlantAndEquipment",), "duration", "USD"),
    "capex_oil_gas": (("PaymentsToAcquireOilAndGasProperty",), "duration", "USD"),
    "capex_other_ppe": (("PaymentsToAcquireOtherPropertyPlantAndEquipment",), "duration", "USD"),
    "cash": (("CashAndCashEquivalentsAtCarryingValue",), "instant", "USD"),
    "diluted_shares": (("WeightedAverageNumberOfDilutedSharesOutstanding",), "duration", "shares"),
    "receivables": (("AccountsReceivableNetCurrent",), "instant", "USD"),
    "inventory": (("InventoryNet",), "instant", "USD"),
    "accounts_payable": (("AccountsPayableCurrent",), "instant", "USD"),
    "debt_current": (("LongTermDebtCurrent",), "instant", "USD"),
    "debt_noncurrent": (("LongTermDebtNoncurrent",), "instant", "USD"),
    "operating_leases_current": (("OperatingLeaseLiabilityCurrent",), "instant", "USD"),
    "operating_leases_noncurrent": (("OperatingLeaseLiabilityNoncurrent",), "instant", "USD"),
    "finance_leases_current": (("FinanceLeaseLiabilityCurrent",), "instant", "USD"),
    "finance_leases_noncurrent": (("FinanceLeaseLiabilityNoncurrent",), "instant", "USD"),
    "assets": (("Assets",), "instant", "USD"),
    "liabilities": (("Liabilities",), "instant", "USD"),
    "equity": (("StockholdersEquity",), "instant", "USD"),
    "ppe": (("PropertyPlantAndEquipmentNet",), "instant", "USD"),
    "acquisitions": (("PaymentsToAcquireBusinessesNetOfCashAcquired",), "duration", "USD"),
    "asset_sales": (("ProceedsFromSaleOfPropertyPlantAndEquipment",), "duration", "USD"),
}


class HistoryError(ValueError):
    pass


@dataclass
class History:
    annual: pd.DataFrame
    audit: pd.DataFrame
    warnings: list[str]
    provider: str
    raw_path: Path
    retrieved_at: str


def _number(value):
    return pd.to_numeric(value, errors="coerce") if value not in (None, "") else float("nan")


def _latest_wrds(root, ticker):
    matches = []
    for path in (Path(root) / "data/raw/wrds").glob("*/verification.json"):
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("test_ticker", "").upper() == ticker.upper():
            matches.append((report.get("retrieved_at_utc", ""), path, report))
    if not matches:
        raise HistoryError(f"No saved WRDS report for {ticker}. Run python -m app.refresh_wrds --ticker {ticker}.")
    _, path, report = max(matches, key=lambda x: (x[0], str(x[1])))
    if report.get("compustat", {}).get("status") != "available":
        raise HistoryError("Latest matching WRDS refresh is not available; inspect its report. Older data was not substituted.")
    return report, path


def normalize_wrds(report, ticker, as_of):
    rows, seen, gvkeys = [], set(), set()
    for record in report["compustat"]["records"]:
        end = date.fromisoformat(str(record["datadate"])[:10])
        if end > as_of:
            continue
        if record.get("tic") and record["tic"].upper() != ticker.upper():
            raise HistoryError("WRDS record ticker differs from the selected company.")
        if record.get("curcd") != "USD":
            raise HistoryError("This normalizer expects USD Compustat financials; currency conversion is not implemented.")
        gvkeys.add(record["gvkey"])
        year = int(record["fyear"])
        if year in seen:
            raise HistoryError("Duplicate WRDS fiscal year; resolve source records before normalization.")
        seen.add(year)
        for metric, field in WRDS_FIELDS.items():
            value = _number(record.get(field))
            rows.append(dict(year=year, metric=metric, value=value,
                             original_value=record.get(field), source_field=field,
                             original_units="million shares" if metric == "diluted_shares" else "USD millions",
                             period_start=None, period_end=end.isoformat(), filed=None,
                             source=report["compustat"]["table"], accession=None,
                             status="reported" if pd.notna(value) else "missing field/value"))
    if len(gvkeys) > 1:
        raise HistoryError("WRDS report contains multiple company identities.")
    return rows


def _eligible(fact, kind, end, as_of):
    if fact.get("form") not in {"10-K", "10-K/A"} or fact.get("end") != end:
        return False
    if not fact.get("filed") or fact["filed"] > as_of.isoformat() or fact["end"] > as_of.isoformat():
        return False
    if kind == "instant":
        return not fact.get("start")
    if not fact.get("start"):
        return False
    days = (date.fromisoformat(end) - date.fromisoformat(fact["start"])).days
    return 330 <= days <= 380


def normalize_sec(payload, as_of):
    company = payload["companyfacts"]
    concepts = company["facts"]["us-gaap"]
    # Use the fact's actual reporting period, NEVER the filing's 'fy' field.
    # A later filing labels comparative prior-year facts with its own FY.
    anchors = concepts.get("NetIncomeLoss", {}).get("units", {}).get("USD", [])
    ends = sorted({f["end"] for f in anchors if _eligible(f, "duration", f.get("end"), as_of)})
    year_ends = {}
    for end in ends:
        year = date.fromisoformat(end).year
        if year in year_ends and year_ends[year] != end:
            raise HistoryError("Multiple annual period ends in one calendar year; fiscal-year transition needs review.")
        year_ends[year] = end
    rows = []
    for year, end in year_ends.items():
        for metric, (tags, kind, unit) in SEC_FIELDS.items():
            candidates = [dict(f, tag=tag) for tag in tags
                          for f in concepts.get(tag, {}).get("units", {}).get(unit, [])
                          if _eligible(f, kind, end, as_of)]
            if candidates:
                # Prefer the newest filed disclosure; reject conflicting values
                # within that filing rather than choosing by list order.
                rank = max((f["filed"], f.get("accn", "")) for f in candidates)
                latest = [f for f in candidates if (f["filed"], f.get("accn", "")) == rank]
                if len({(f.get("start"), f["val"]) for f in latest}) > 1:
                    raise HistoryError(f"Ambiguous SEC facts for {metric}, period ending {end}.")
                fact = latest[0]
                accession = fact["accn"]
                source = f"https://www.sec.gov/Archives/edgar/data/{int(company['cik'])}/{accession.replace('-', '')}/{accession}-index.html"
                rows.append(dict(year=year, metric=metric, value=float(fact["val"]) / 1_000_000,
                                 original_value=fact["val"], source_field=f"us-gaap:{fact['tag']}",
                                 original_units=unit,
                                 period_start=fact.get("start"), period_end=end,
                                 filed=fact["filed"], accession=accession, source=source, status="reported"))
            else:
                rows.append(dict(year=year, metric=metric, value=float("nan"), original_value=None,
                                 source_field=" | ".join(tags), period_start=None, period_end=end,
                                 filed=None, accession=None, source=None, status="standard concept unavailable"))
    return rows


def load_history(ticker="EOG", years=5, *, provider="wrds", cik=None,
                 refresh=False, end_year=None, root=PROJECT_ROOT, as_of=None):
    """Select latest reported years, retaining a preceding year for differences.

    This is a latest-disclosure research view, not a point-in-time backtest.
    SEC uses year of period end; WRDS uses provider fiscal-year labels.
    """
    if type(years) is not int or not 1 <= years <= 9:
        raise HistoryError("years must be an integer from 1 to 9.")
    if end_year is not None and (type(end_year) is not int or end_year < 1900):
        raise HistoryError("end_year must be an integer year or None.")
    ticker = ticker.strip().upper()
    as_of = as_of or date.today()
    provider = provider.lower()
    if provider == "wrds":
        if refresh:
            raise HistoryError("Refresh WRDS in interactive PowerShell with python -m app.refresh_wrds; then rerun this cell.")
        payload, path = _latest_wrds(root, ticker)
        rows = normalize_wrds(payload, ticker, as_of)
        warnings = ["WRDS values use Compustat definitions. dlc + dltt is provider debt; do not add leases again without a bridge."]
    elif provider == "sec":
        payload, path = load_companyfacts(cik, ticker, refresh=refresh, root=root)
        rows = normalize_sec(payload, as_of)
        warnings = ["SEC uses the latest available 10-K disclosure per concept and period. Inspect source tags and filing dates; custom tags remain unavailable.",
                    "SEC years are labeled by calendar year of the fiscal period end."]
    else:
        raise HistoryError("provider must be 'wrds' or 'sec'.")
    if not rows:
        raise HistoryError("No eligible annual records in this source.")
    audit = pd.DataFrame(rows)
    audit["provider"] = provider.upper()
    audit["retrieved_at_utc"] = payload["retrieved_at_utc"]
    audit["units"] = audit["metric"].map(lambda m: "million shares" if m == "diluted_shares" else "USD millions")
    data = audit.pivot(index="year", columns="metric", values="value").sort_index()
    last = end_year if end_year is not None else int(data.index.max())
    selected = list(range(last - years + 1, last + 1))
    full_years = [selected[0] - 1, *selected]
    absent = [y for y in full_years if y not in data.index]
    if absent:
        warnings.append(f"No annual record for {absent}; missing years and dependent changes stay blank.")
    data = data.reindex(full_years)
    for metric in set(WRDS_FIELDS) | set(SEC_FIELDS):
        if metric not in data:
            data[metric] = float("nan")
    if provider == "sec":
        # Sum only when both components exist. Missing is never zero.
        fallback = data["capex_oil_gas"] + data["capex_other_ppe"]
        for y in data.index[data["capex"].isna() & fallback.notna()]:
            data.loc[y, "capex"] = fallback.loc[y]
            audit = pd.concat([audit, pd.DataFrame([dict(year=y, metric="capex", value=fallback.loc[y],
                source_field="capex_oil_gas + capex_other_ppe", status="calculated; inspect component sources",
                units="USD millions", provider="SEC", retrieved_at_utc=payload["retrieved_at_utc"])])], ignore_index=True)
    formulas = {
        "ebitda": ("ebit + dd_a", lambda d: d.ebit + d.dd_a),
        "total_debt": ("debt_current + debt_noncurrent (provider definition)", lambda d: d.debt_current + d.debt_noncurrent),
        "operating_leases": ("operating_leases_current + operating_leases_noncurrent", lambda d: d.operating_leases_current + d.operating_leases_noncurrent),
        "nwc_proxy": ("receivables + inventory - accounts_payable", lambda d: d.receivables + d.inventory - d.accounts_payable),
        "change_nwc_proxy": ("nwc_proxy - previous consecutive year's nwc_proxy", lambda d: d.nwc_proxy.diff()),
        "revenue_growth_pct": ("100 * (revenue / previous year's revenue - 1)", lambda d: 100 * (d.revenue / d.revenue.shift().replace(0, float('nan')) - 1)),
        "ebit_margin_pct": ("100 * ebit / revenue", lambda d: 100 * d.ebit / d.revenue.replace(0, float('nan'))),
        "ebitda_margin_pct": ("100 * ebitda / revenue", lambda d: 100 * d.ebitda / d.revenue.replace(0, float('nan'))),
        "effective_tax_rate_pct": ("100 * income_tax / pretax_income", lambda d: 100 * d.income_tax / d.pretax_income.replace(0, float('nan'))),
        "capex_revenue_pct": ("100 * capex / revenue", lambda d: 100 * d.capex / d.revenue.replace(0, float('nan'))),
        "net_debt": ("total_debt - cash (same debt scope)", lambda d: d.total_debt - d.cash),
    }
    for metric, (formula, calculate) in formulas.items():
        data[metric] = calculate(data)
        units = "percent" if metric.endswith("_pct") else "USD millions"
        calculated = pd.DataFrame([dict(year=y, metric=metric, value=data.loc[y, metric],
            source_field=formula, status="calculated" if pd.notna(data.loc[y, metric]) else "missing inputs",
            units=units, provider=provider.upper(), retrieved_at_utc=payload["retrieved_at_utc"])
            for y in full_years])
        audit = pd.concat([audit, calculated], ignore_index=True)
    data.index.name = "Fiscal year"
    warnings.append("NWC is an AR + inventory - AP proxy. Acquisitions, capital payables and omitted current accounts need review before UFCF.")
    warnings.append("Refreshing a raw source is required to discover a newly reported year; a cached run cannot download new information.")
    return History(data.loc[selected], audit[audit.year.isin(full_years)].reset_index(drop=True),
                   warnings, provider.upper(), path, payload["retrieved_at_utc"])


def load_operating_observations(path, years, ticker="EOG"):
    """Join reviewed disclosures by year; never carry old values into new years."""
    if not Path(path).exists():
        return pd.DataFrame(index=pd.Index(years, name="Fiscal year")), pd.DataFrame()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    records = pd.DataFrame(payload["observations"])
    if records.empty:
        return pd.DataFrame(index=pd.Index(years, name="Fiscal year")), records
    if not {"ticker", "year", "metric", "value"}.issubset(records.columns):
        raise HistoryError("Operating observations require ticker, year, metric, and value.")
    records = records[records.ticker.eq(ticker.upper())].copy()
    if records.duplicated(["year", "metric"]).any():
        raise HistoryError("Duplicate operating observations; resolve source versions first.")
    if records.empty:
        return pd.DataFrame(index=pd.Index(years, name="Fiscal year")), records
    data = records.pivot(index="year", columns="metric", values="value").reindex(years)
    data.index.name = "Fiscal year"
    return data, records[records.year.isin(years)]
