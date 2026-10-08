"""Retrieve EOG revenue components and volume/price disclosures from annual filings.

Financial components use inline XBRL with exact product dimensions. Operating
inputs use the labeled MD&A daily-volume table, which is not tagged numerically.
The adapter fails on unfamiliar layouts instead of inferring missing values.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
import math
import re
import xml.etree.ElementTree as ET

import pandas as pd

from app.sec import PROJECT_ROOT, SECError
from app.sec_filings import load_annual_filing

NS = {"x": "http://www.xbrl.org/2003/instance", "d": "http://xbrl.org/2006/xbrldi",
      "ix": "http://www.xbrl.org/2013/inlineXBRL", "h": "http://www.w3.org/1999/xhtml"}
PRODUCT_AXIS = "srt:ProductOrServiceAxis"
SALES = "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax"
OTHER = "us-gaap:RevenueNotFromContractWithCustomer"
FINANCIAL_TAGS = {
    (SALES, "us-gaap:OilAndCondensateMember"): "oil_sales",
    (SALES, "us-gaap:NaturalGasProductionMember"): "gas_sales",
    (SALES, "srt:NaturalGasLiquidsReservesMember"): "ngl_sales",
    (SALES, "us-gaap:NaturalGasGatheringTransportationMarketingAndProcessingMember"): "marketing_revenue",
    (OTHER, "eog:GainsLossesOnMarkToMarketCommodityDerivativeContractsMember"): "derivative_gain_loss",
    (OTHER, "eog:GainsLossesOnMarkToMarketCommodityDerivativeContractsAndOtherDerivativeContractsNetMember"): "derivative_gain_loss",
    (OTHER, "eog:OtherNetMember"): "other_revenue",
    ("us-gaap:GainLossOnDispositionOfAssets1", ""): "disposition_gain_loss",
    ("us-gaap:Revenues", ""): "reported_revenue",
    ("eog:MarketingCosts", ""): "marketing_costs",
}
OPERATING_ROWS = {
    "Crude Oil and Condensate Volumes (MBbld)": ("oil_mbbl_day", "Total", "MBbl/day"),
    "Natural Gas Volumes (MMcfd)": ("gas_mmcf_day", "Total", "MMcf/day"),
    "Natural Gas Liquids Volumes (MBbld)": ("ngl_mbbl_day", "Total", "MBbl/day"),
    "Average Crude Oil and Condensate Prices ($/Bbl)": ("realized_oil", "Composite", "USD/Bbl"),
    "Average Natural Gas Prices ($/Mcf)": ("realized_gas", "Composite", "USD/Mcf"),
    "Average Natural Gas Liquids Prices ($/Bbl)": ("realized_ngl", "Composite", "USD/Bbl"),
}
FINANCIAL_FIELDS = list(dict.fromkeys(FINANCIAL_TAGS.values()))
OPERATING_FIELDS = [spec[0] for spec in OPERATING_ROWS.values()]


def _text(element):
    return " ".join("".join(element.itertext()).split())


def _decimal(text):
    try:
        value = Decimal(text.replace(",", "").replace(" ", "").replace("\u00a0", ""))
        if not value.is_finite():
            raise InvalidOperation
        return value
    except InvalidOperation:
        raise SECError(f"Unsupported numeric disclosure: {text!r}.") from None


def read_revenue_filing(document, metadata):
    """Return value-level audit rows; preserve published precision and dates."""
    if str(metadata["cik"]).zfill(10) != "0000821189":
        raise SECError("The revenue disclosure adapter currently supports EOG only.")
    try:
        root = ET.fromstring(document)
    except ET.ParseError:
        raise SECError("Could not parse EOG's filing XML.") from None
    base = {"provider": "SEC", "accession": metadata["accession"], "filed": metadata["filed"],
            "retrieved_at_utc": metadata["retrieved_at_utc"], "filing_report_date": metadata["report_date"]}
    contexts = {}
    for context in root.findall(".//x:context", NS):
        identifier = context.findtext(".//x:identifier", namespaces=NS)
        if not identifier or identifier.zfill(10) != str(metadata["cik"]).zfill(10):
            continue
        if context.find(".//d:typedMember", NS) is not None:
            continue
        dimensions = {e.attrib["dimension"]: e.text for e in context.findall(".//d:explicitMember", NS)}
        if set(dimensions) - {PRODUCT_AXIS}:
            continue
        start = context.findtext(".//x:startDate", namespaces=NS)
        end = context.findtext(".//x:endDate", namespaces=NS)
        if not start or not end:
            continue
        # Supported EOG annual periods: calendar years, never quarters or acquisition periods.
        year = date.fromisoformat(end).year
        if start != f"{year}-01-01" or end != f"{year}-12-31":
            continue
        contexts[context.attrib["id"]] = (year, start, end, dimensions.get(PRODUCT_AXIS, ""))
    units = {e.attrib["id"]: [m.text for m in e.findall("x:measure", NS)]
             for e in root.findall(".//x:unit", NS)}
    records = []
    for element in root.findall(".//ix:nonFraction", NS):
        context = contexts.get(element.attrib.get("contextRef"))
        if context is None:
            continue
        year, start, end, member = context
        tag = element.attrib.get("name")
        metric = FINANCIAL_TAGS.get((tag, member))
        if metric is None:
            continue
        if units.get(element.attrib.get("unitRef")) != ["iso4217:USD"]:
            raise SECError(f"Unexpected currency/unit for {metric}.")
        text = _text(element)
        transform = element.attrib.get("format", "").split(":")[-1]
        nil = element.attrib.get("{http://www.w3.org/2001/XMLSchema-instance}nil") in ("true", "1")
        if nil:
            value = float("nan")
        else:
            if transform in ("zerodash", "fixed-zero"):
                number = Decimal(0)
            elif transform in ("", "num-dot-decimal", "numdotdecimal"):
                number = _decimal(text)
            else:
                raise SECError(f"Unsupported inline number format for {metric}: {transform}.")
            sign = -1 if element.attrib.get("sign") == "-" else 1
            value = float(number * sign * Decimal(10) ** int(element.attrib.get("scale", "0")) / Decimal(1_000_000))
            if not math.isfinite(value):
                raise SECError(f"Nonfinite value for {metric}.")
        decimals = element.attrib.get("decimals", "INF")
        precision = 0.0 if decimals == "INF" else 0.5 * 10 ** (-int(decimals)) / 1_000_000
        records.append({**base, "year": year, "metric": metric, "value": value, "units": "USD millions",
                        "half_rounding_unit": precision, "period_start": start, "period_end": end,
                        "source_field": tag, "product_member": member, "context_id": element.attrib["contextRef"],
                        "original_text": text, "original_units": "USD", "scale": element.attrib.get("scale", "0"),
                        "sign": element.attrib.get("sign", "+"), "decimals": decimals,
                        "source": metadata["url"] + "#" + element.attrib.get("id", "")})

    tables = [t for t in root.findall(".//h:table", NS)
              if "Crude Oil and Condensate Volumes (MBbld)" in _text(t)
              and "Average Natural Gas Prices ($/Mcf)" in _text(t)]
    if len(tables) != 1:
        raise SECError("Expected one labeled daily-volume/price table; review the filing layout.")
    years = None
    section = None
    section_label = None
    operating_records = []
    for row_number, row in enumerate(tables[0].findall(".//h:tr", NS), start=1):
        cells = [_text(td) for td in row.findall("./h:td", NS)]
        cells = [text for text in cells if text and text != "$"]
        if not cells:
            continue
        label = cells[0]
        if label == "Year Ended December 31":
            if years is not None or not all(re.fullmatch(r"\d{4}", y) for y in cells[1:]):
                raise SECError("Ambiguous year header in EOG volume/price table.")
            years = [int(y) for y in cells[1:]]
            if not years or len(set(years)) != len(years):
                raise SECError("Missing or duplicate fiscal years in EOG volume/price table.")
            continue
        header = re.sub(r"\s*\(\d+\)$", "", label)
        if "Volumes (" in header or "Prices (" in header:
            section = OPERATING_ROWS.get(header)
            section_label = header
            continue
        if section is None or label != section[1]:
            continue
        if years is None or len(cells) - 1 != len(years):
            raise SECError("Volume/price values do not match the filing's year columns.")
        for year, text in zip(years, cells[1:]):
            # A dash is unavailable here, never an assumed zero price or volume.
            missing = text in ("-", "—", "–")
            number = None if missing else _decimal(text)
            value = float("nan") if missing else float(number)
            precision = float("nan") if missing else float(Decimal("0.5") * Decimal(10) ** number.as_tuple().exponent)
            operating_records.append({**base, "year": year, "metric": section[0], "value": value,
                                      "units": section[2], "half_rounding_unit": precision,
                                      "period_start": f"{year}-01-01", "period_end": f"{year}-12-31",
                                      "source_field": f"MD&A daily volume/price table: {section_label} / {label}",
                                      "table_row": row_number, "original_text": text, "original_units": section[2],
                                      "scale": "0", "source": metadata["url"]})
    for year in years or []:
        found = [r["metric"] for r in operating_records if r["year"] == year]
        if sorted(found) != sorted(OPERATING_FIELDS):
            raise SECError(f"Incomplete or duplicate volume/price rows for FY{year}.")
    if not operating_records:
        raise SECError("No supported volume/price observations found.")
    audit = pd.DataFrame(records + operating_records)
    keys = ["year", "metric"]
    if (audit.groupby(keys)["value"].nunique(dropna=False) > 1).any():
        raise SECError("Conflicting consolidated revenue facts; review the filing contexts.")
    # Keep the most precise occurrence when identical facts are displayed twice.
    return audit.sort_values("half_rounding_unit").drop_duplicates(keys).sort_values(keys).reset_index(drop=True)


def load_revenue_history(years, *, cik, ticker, refresh=False, root=PROJECT_ROOT):
    """Use each filing's comparative years, then fetch earlier filings as needed.

    All metrics for a year come from one filing, chosen newest report first.
    This is a selected-filings historical view, not a point-in-time dataset or a
    promise to incorporate every later restatement. Notebook compares total
    revenue to the separate current companyfacts series to expose differences.
    """
    if ticker.upper() != "EOG":
        raise SECError("The revenue disclosure adapter currently supports EOG only.")
    requested = sorted(set(int(y) for y in years))
    if not requested:
        raise ValueError("Select at least one fiscal year.")
    remaining = set(requested)
    packets = []
    for year in reversed(requested):
        if year not in remaining:
            continue
        document, metadata, _ = load_annual_filing(cik, ticker, year, refresh=refresh, root=root)
        facts = read_revenue_filing(document, metadata)
        covered = set(facts.loc[facts.metric.eq("reported_revenue"), "year"]) & remaining
        if year not in covered:
            raise SECError(f"FY{year} filing lacks a supported consolidated revenue fact.")
        packets.append(facts[facts.year.isin(covered)])
        remaining -= covered
    audit = pd.concat(packets, ignore_index=True)
    fields = FINANCIAL_FIELDS + OPERATING_FIELDS
    data = audit.pivot(index="year", columns="metric", values="value").reindex(index=requested, columns=fields)
    precision = audit.pivot(index="year", columns="metric", values="half_rounding_unit").reindex(index=requested, columns=fields)
    data.columns.name = precision.columns.name = None
    return data, precision, audit
