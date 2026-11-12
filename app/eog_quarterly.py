"""EOG quarterly supplemental tables from an earnings 8-K exhibit (EX-99.1).

The exhibit's financial tables are HTML text, so values are parsed by row label
inside named table sections. Q1-Q3 + year-to-date columns are checked against
each other; a missing row or an unexpected column count raises an error rather
than producing a substituted value. Amounts are USD millions unless the row
units say otherwise (MBbl/d, MMcf/d, USD/Bbl, USD/Mcf).
"""

import html
import re

import pandas as pd

from app.sec import PROJECT_ROOT, SECError
from app.sec_filings import load_filing_document


def document_text(document_bytes):
    """Normalize an HTML filing to one line of text with ' | ' between table cells.

    The same normalization is used by guidance quote checks, so a quoted row is
    reproducible from the cached document.
    """
    try:
        text = document_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = document_bytes.decode("cp1252", errors="replace")
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = re.sub(r"(?is)<ix:header>.*?</ix:header>", " ", text)
    text = re.sub(r"(?i)</t[dh]\s*>", " | ", text)
    text = re.sub(r"(?i)</(tr|p|div)\s*>|<br\s*/?>", " | ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = (text.replace("\xa0", " ").replace("’", "'").replace("‘", "'")
            .replace("“", '"').replace("”", '"').replace("–", "-"))
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(\s*\|\s*)+", " | ", text)
    return text.strip()


def _number(token):
    token = token.strip().replace(",", "")
    if token in {"—", "-", "―"}:
        return 0.0  # The tables print an em dash for a nil amount.
    negative = token.startswith("(") and token.endswith(")")
    token = token.strip("()").rstrip("%")
    try:
        value = float(token)
    except ValueError:
        return None
    return -value if negative else value


def _row(section, label, *, after=None, column_count=None):
    start = 0
    if after:
        start = section.find(after)
        if start < 0:
            raise SECError(f"Supplemental table anchor not found: {after!r}.")
    match = re.compile(r"\| " + re.escape(label) + r" \|").search(section, start)
    if not match:
        raise SECError(f"Supplemental table row not found: {label!r}.")
    values = []
    for token in section[match.end():].split(" | "):
        token = token.strip()
        if token == "$":
            continue
        number = _number(token)
        if number is None:
            break
        values.append(number)
    if column_count is not None and len(values) != column_count:
        raise SECError(f"Row {label!r} has {len(values)} values; expected {column_count}.")
    return values, section[match.start():match.end()]


SECTIONS = {
    "income": ("Income Statements | In millions of USD", "Volumes and Prices | (Unaudited)"),
    "volumes": ("Volumes and Prices | (Unaudited)", "Balance Sheets | In millions of USD"),
    "balance": ("Balance Sheets | In millions of USD", "Cash Flow Statements | In millions of USD"),
    "cashflow": ("Cash Flow Statements | In millions of USD", "Non-GAAP Financial Measures |"),
    "capex": ("See below reconciliation of Total Expenditures (GAAP)", "Cash Flow from Operations and Free Cash Flow"),
}

# metric: (section, label, anchor within section or None, units)
ROWS = {
    "oil_sales": ("income", "Crude Oil and Condensate", None, "USD millions"),
    "ngl_sales": ("income", "Natural Gas Liquids", None, "USD millions"),
    "gas_sales": ("income", "Natural Gas", None, "USD millions"),
    "derivative_gain_loss": ("income", "Gains (Losses) on Mark-to-Market Financial Commodity and Other Derivative Contracts, Net", None, "USD millions"),
    "marketing_revenue": ("income", "Gathering, Processing and Marketing", None, "USD millions"),
    "disposition_gain_loss": ("income", "Gains (Losses) on Asset Dispositions, Net", None, "USD millions"),
    "other_revenue": ("income", "Other, Net", "Gains (Losses) on Asset Dispositions", "USD millions"),
    "reported_revenue": ("income", "Total", "Operating Revenues and Other", "USD millions"),
    "lease_well": ("income", "Lease and Well", None, "USD millions"),
    "gpt": ("income", "Gathering, Processing and Transportation Costs", None, "USD millions"),
    "exploration": ("income", "Exploration Costs", None, "USD millions"),
    "dry_hole": ("income", "Dry Hole Costs", None, "USD millions"),
    "impairments": ("income", "Impairments", None, "USD millions"),
    "marketing_costs": ("income", "Marketing Costs", None, "USD millions"),
    "dd_a": ("income", "Depreciation, Depletion and Amortization", None, "USD millions"),
    "ga": ("income", "General and Administrative", None, "USD millions"),
    "taxes_other_than_income": ("income", "Taxes Other Than Income", None, "USD millions"),
    "reported_operating_expenses": ("income", "Total", "Operating Expenses", "USD millions"),
    "reported_operating_income": ("income", "Operating Income", None, "USD millions"),
    "interest_expense": ("income", "Interest Expense, Net", None, "USD millions"),
    "pretax_income": ("income", "Income Before Income Taxes", None, "USD millions"),
    "income_tax": ("income", "Income Tax Provision", None, "USD millions"),
    "net_income": ("income", "Net Income", "Income Tax Provision", "USD millions"),
    "diluted_shares": ("income", "Diluted", "Average Number of Common Shares", "millions"),
    "basic_shares": ("income", "Basic", "Average Number of Common Shares", "millions"),
    "oil_mbbl_day": ("volumes", "Total", "Crude Oil and Condensate Volumes (MBod)", "MBbl/day"),
    "realized_oil": ("volumes", "Composite", "Average Crude Oil and Condensate Prices", "USD/Bbl"),
    "ngl_mbbl_day": ("volumes", "Total", "Natural Gas Liquids Volumes (MBbld)", "MBbl/day"),
    "realized_ngl": ("volumes", "Composite", "Average Natural Gas Liquids Prices", "USD/Bbl"),
    "gas_mmcf_day": ("volumes", "Total", "Natural Gas Volumes (MMcfd)", "MMcf/day"),
    "us_gas_mmcf_day": ("volumes", "United States", "Natural Gas Volumes (MMcfd)", "MMcf/day"),
    "realized_gas": ("volumes", "Composite", "Average Natural Gas Prices", "USD/Mcf"),
    "realized_us_gas": ("volumes", "United States", "Average Natural Gas Prices", "USD/Mcf"),
    "total_mboe_day": ("volumes", "Total", "Crude Oil Equivalent Volumes (MBoed)", "MBoe/day"),
    "operating_cash_flow": ("cashflow", "Net Cash Provided by Operating Activities", None, "USD millions"),
    "stock_compensation": ("cashflow", "Stock-Based Compensation Expenses", None, "USD millions"),
    "deferred_tax": ("cashflow", "Deferred Income Taxes", None, "USD millions"),
    "oil_gas_additions": ("cashflow", "Additions to Oil and Gas Properties", None, "USD millions"),
    "other_ppe_additions": ("cashflow", "Additions to Other Property, Plant and Equipment", None, "USD millions"),
    "asset_sale_proceeds": ("cashflow", "Proceeds from Sales of Assets", None, "USD millions"),
    "capex_nongaap": ("capex", "Total Capital Expenditures (Non-GAAP)", None, "USD millions"),
    "asset_retirement_costs": ("capex", "Asset Retirement Costs", None, "USD millions"),
}

BALANCE_ROWS = {
    "cash": ("Cash and Cash Equivalents", None),
    "receivables": ("Accounts Receivable, Net", None),
    "inventory": ("Inventories", None),
    "accounts_payable": ("Accounts Payable", None),
    "accrued_taxes_payable": ("Accrued Taxes Payable", None),
    "dividends_payable": ("Dividends Payable", None),
    "debt_current": ("Current Portion of Long-Term Debt", None),
    "debt_noncurrent": ("Long-Term Debt", "Current Portion of Operating Lease Liabilities"),
    "equity": ("Total Stockholders' Equity", None),
}

FLOW_UNITS = {"USD millions"}


def _section(text, name):
    begin, end = SECTIONS[name]
    i = text.find(begin)
    j = text.find(end, i + len(begin)) if i >= 0 else -1
    if i < 0 or j < 0:
        raise SECError(f"Supplemental {name} table not found; the exhibit layout needs review.")
    # Drop the printed page number that precedes the next table's title.
    return re.sub(r"\| \d{1,3} \| ?$", "| ", text[i:j])


def _years(section):
    found = re.findall(r"\| (20\d\d)(?= \|)", section[:400])
    if len(found) < 2:
        raise SECError("Supplemental table year header not found.")
    return int(found[0]), int(found[1])


def parse_supplemental(document_bytes):
    """Return (quarterly flows, quarter-end balances, audit, checks)."""
    text = document_text(document_bytes)
    income = _section(text, "income")
    prior, current = _years(income)
    if current != prior + 1:
        raise SECError("Unexpected supplemental year columns.")
    sections = {name: _section(text, name) for name in SECTIONS}
    rows, audit = {}, []
    expected = None
    for metric, (section_name, label, anchor, units) in ROWS.items():
        values, quoted = _row(sections[section_name], label, after=anchor)
        if section_name == "capex":
            values = [abs(v) for v in values]
        if expected is None:
            expected = len(values)
        if len(values) != expected or not 7 <= expected <= 10:
            raise SECError(f"Row {label!r} has {len(values)} values; expected {expected}.")
        rows[metric] = values
        audit.append({"metric": metric, "label": label, "anchor": anchor, "section": section_name,
                      "units": units, "quoted_row_start": quoted})
    quarters = expected - 6  # prior Q1-Q4 + FY, then k quarters + YTD/year
    labels = [f"{prior}Q{q}" for q in range(1, 5)] + [f"{prior}FY"]
    labels += [f"{current}Q{q}" for q in range(1, quarters + 1)]
    labels += [f"{current}FY" if quarters == 4 else f"{current}YTD"]
    flows = pd.DataFrame(rows, index=labels)

    balance_section = sections["balance"]
    balance_rows = {}
    for metric, (label, anchor) in BALANCE_ROWS.items():
        values, quoted = _row(balance_section, label, after=anchor, column_count=4 + quarters)
        balance_rows[metric] = values
        audit.append({"metric": metric, "label": label, "anchor": anchor, "section": "balance",
                      "units": "USD millions", "quoted_row_start": quoted})
    ends = [f"{prior}-03-31", f"{prior}-06-30", f"{prior}-09-30", f"{prior}-12-31",
            f"{current}-03-31", f"{current}-06-30", f"{current}-09-30", f"{current}-12-31"][:4 + quarters]
    balances = pd.DataFrame(balance_rows, index=pd.Index(ends, name="period_end"))

    checks = []
    for metric, (_, _, _, units) in ROWS.items():
        if units not in FLOW_UNITS:
            continue
        for year, n, total_label in [(prior, 4, f"{prior}FY"), (current, quarters, labels[-1])]:
            parts = flows.loc[[f"{year}Q{q}" for q in range(1, n + 1)], metric].sum()
            residual = parts - flows.at[total_label, metric]
            # Each published figure is rounded to $1 million.
            bound = 0.5 * (n + 1)
            checks.append({"metric": metric, "period": total_label, "quarter sum less total": residual,
                           "rounding bound": bound,
                           "Status": "Ties within rounding" if abs(residual) <= bound else "Review difference"})
    return flows, balances, pd.DataFrame(audit), pd.DataFrame(checks)


def load_supplemental(cik, ticker, accession, document, *, refresh=False, root=PROJECT_ROOT):
    document_bytes, metadata, path = load_filing_document(cik, ticker, accession, document=document,
                                                          refresh=refresh, root=root)
    flows, balances, audit, checks = parse_supplemental(document_bytes)
    for key in ("accession", "form", "filed", "url", "retrieved_at_utc"):
        audit[key] = metadata.get(key)
    return flows, balances, audit, checks
