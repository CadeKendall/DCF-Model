"""EOG acquisition disclosures and diagnostic bridges, separate from forecasts.

Values come from the selected annual filing's inline XBRL, including dimensions
that SEC companyfacts does not expose. No acquisition amounts or years are stored
in the mapping. Unsupported or ambiguous disclosure structures require review.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
import xml.etree.ElementTree as ET

import pandas as pd

from app.sec import SECError

NS = {"x": "http://www.xbrl.org/2003/instance",
      "d": "http://xbrl.org/2006/xbrldi",
      "ix": "http://www.xbrl.org/2013/inlineXBRL"}
AXIS = "us-gaap:BusinessAcquisitionAxis"
LOCATION = "us-gaap:IncomeStatementLocationAxis"
BASE = "us-gaap:BusinessCombinationRecognizedIdentifiableAssetsAcquiredAndLiabilitiesAssumed"
TAGS = {
    "consideration": "us-gaap:BusinessCombinationConsiderationTransferred1",
    "acquired_cash": BASE + "CashAndEquivalents",
    "acquired_receivables": BASE + "CurrentAssetsReceivables",
    "acquired_inventory": BASE + "Inventory",
    "acquired_payables": BASE + "CurrentLiabilitiesAccountsPayable",
    "acquisition_costs": "us-gaap:BusinessCombinationAcquisitionRelatedCosts",
    "acquiree_revenue_since_close": "us-gaap:BusinessCombinationProFormaInformationRevenueOfAcquireeSinceAcquisitionDateActual",
    "acquiree_net_income_since_close": "us-gaap:BusinessCombinationProFormaInformationEarningsOrLossOfAcquireeSinceAcquisitionDateActual",
    "proforma_revenue": "us-gaap:BusinessAcquisitionsProFormaRevenue",
    "proforma_net_income": "us-gaap:BusinessAcquisitionsProFormaNetIncomeLoss",
    "reported_ga": "us-gaap:GeneralAndAdministrativeExpense",
}


def read_eog_acquisitions(document, metadata):
    """Return extracted facts and acquisition-date balances for the report year.

    This adapter supports EOG's observed USD, dot-decimal inline XBRL structure.
    Missing values remain NaN. Acquisition dates must be identifiable from an
    undivided consideration fact with a one-day context, not inferred from year
    end. Comparative disclosures are preserved but never reapplied as new deals.
    """
    if str(metadata["cik"]).zfill(10) != "0000821189":
        raise SECError("The acquisition disclosure mapping currently supports EOG only.")
    try:
        root = ET.fromstring(document)
    except ET.ParseError:
        raise SECError("Filing XML could not be parsed. Review the document; no amounts were inferred.") from None
    contexts = {}
    for element in root.findall(".//x:context", NS):
        if element.find(".//d:typedMember", NS) is not None:
            continue
        identifier = element.findtext(".//x:identifier", namespaces=NS)
        if identifier is None or identifier.zfill(10) != str(metadata["cik"]).zfill(10):
            continue
        dimensions = {member.attrib["dimension"]: member.text
                      for member in element.findall(".//d:explicitMember", NS)}
        contexts[element.attrib["id"]] = {
            "dimensions": dimensions,
            "period_start": element.findtext(".//x:startDate", namespaces=NS),
            "period_end": element.findtext(".//x:endDate", namespaces=NS),
            "instant": element.findtext(".//x:instant", namespaces=NS),
        }
    units = {element.attrib["id"]: [measure.text for measure in element.findall("x:measure", NS)]
             for element in root.findall(".//x:unit", NS)}
    rows = []
    mapping = {tag: metric for metric, tag in TAGS.items()}
    for element in root.findall(".//ix:nonFraction", NS):
        tag = element.attrib.get("name")
        context = contexts.get(element.attrib.get("contextRef"))
        if tag not in mapping or context is None:
            continue
        dimensions = context["dimensions"]
        if tag == TAGS["reported_ga"]:
            if dimensions:
                continue
        elif AXIS not in dimensions or set(dimensions) - {AXIS, LOCATION}:
            continue
        if units.get(element.attrib.get("unitRef")) != ["iso4217:USD"]:
            raise SECError(f"Unsupported acquisition units for {tag}.")
        text = "".join(element.itertext()).strip()
        transform = element.attrib.get("format", "").split(":")[-1]
        nil = element.attrib.get("{http://www.w3.org/2001/XMLSchema-instance}nil") in ("true", "1")
        if nil:
            value = float("nan")
        elif transform not in ("", "num-dot-decimal", "numdotdecimal", "zerodash", "fixed-zero"):
            raise SECError(f"Unsupported inline number format: {transform}.")
        else:
            try:
                numeric = Decimal(0) if transform in ("zerodash", "fixed-zero") else Decimal(text.replace(",", "").replace("\u00a0", "").replace(" ", ""))
                if element.attrib.get("sign") == "-":
                    numeric = -numeric
                value = float(numeric * Decimal(10) ** int(element.attrib.get("scale", "0")) / Decimal(1_000_000))
                if not numeric.is_finite():
                    raise InvalidOperation
            except (InvalidOperation, ValueError, OverflowError):
                raise SECError(f"Unrecognized acquisition amount for {tag}.") from None
        rows.append({"metric": mapping[tag], "value": value, "units": "USD millions",
                     "provider": "SEC", "original_units": "USD", "accession": metadata["accession"],
                     "deal": dimensions.get(AXIS, ""), "expense_location": dimensions.get(LOCATION, ""),
                     **{key: context[key] for key in ("period_start", "period_end", "instant")},
                     "source_field": tag, "context_id": element.attrib["contextRef"],
                     "original_text": text, "scale": element.attrib.get("scale", "0"),
                     "sign": element.attrib.get("sign", "+"), "decimals": element.attrib.get("decimals"),
                     "source": metadata["url"] + "#" + element.attrib.get("id", ""),
                     "filed": metadata["filed"], "retrieved_at_utc": metadata["retrieved_at_utc"]})
    audit = pd.DataFrame(rows)
    if audit.empty:
        raise SECError("No supported acquisition or consolidated G&A facts found in this filing.")
    # Duplicate display occurrences can be identical; conflicting facts cannot be selected silently.
    keys = ["metric", "deal", "expense_location", "period_start", "period_end", "instant"]
    if (audit.groupby(keys, dropna=False)["value"].nunique(dropna=False) > 1).any():
        raise SECError("Conflicting values for the same acquisition disclosure context.")
    facts = audit.drop_duplicates(keys)
    year = date.fromisoformat(metadata["report_date"]).year
    dates = facts.loc[(facts.metric == "consideration") & facts.expense_location.eq("")
                      & facts.period_start.eq(facts.period_end) & facts.period_end.notna()]
    deals = []
    for member, group in dates.groupby("deal"):
        current = group[group.period_end.str.startswith(f"{year}-")]
        if current.empty:
            continue
        if len(current) != 1:
            raise SECError(f"Multiple acquisition dates for {member}; separate transaction review required.")
        close = current.iloc[0].period_end
        deal_facts = facts[facts.deal.eq(member)]

        def amount(metric, *, location="", instant=False):
            matches = deal_facts.loc[deal_facts.metric.eq(metric) & deal_facts.expense_location.eq(location)]
            if instant:
                matches = matches[matches.instant.eq(close)]
            else:
                matches = matches[matches.period_start.eq(close) & matches.period_end.eq(close)]
            return float(matches.iloc[0].value) if len(matches) == 1 else float("nan")

        deals.append({"deal": member, "year": year, "close_date": close,
                      "consideration": amount("consideration"),
                      **{key: amount(key, instant=True) for key in
                         ("acquired_cash", "acquired_receivables", "acquired_inventory", "acquired_payables")},
                      "ga_transaction_cost": amount("acquisition_costs", location="us-gaap:GeneralAndAdministrativeExpenseMember"),
                      "interest_transaction_cost": amount("acquisition_costs", location="us-gaap:InterestExpenseMember"),
                      "source": metadata["url"], "filed": metadata["filed"]})
    return pd.DataFrame(deals), facts, audit


def acquisition_bridges(annual, operating, deals, facts, *, provider):
    """Return review tables, never overwrite annual history or approve assumptions.

    NWC proxy = receivables + inventory - payables. Subtracting acquired balances
    explains part of the balance-sheet movement; it is not yet cash operating NWC.
    Expense removal is an illustrative candidate, not an accepted forecast input.
    """
    if provider.upper() != "SEC":
        raise ValueError("Use SEC financial history for filing-based acquisition bridges.")
    latest = int(annual.index.max())
    selected = deals.loc[deals.year.eq(latest)].copy() if not deals.empty else deals.copy()
    if selected.empty:
        return {}
    financial = annual.loc[latest]
    selected["acquired_nwc_proxy"] = selected.acquired_receivables + selected.acquired_inventory - selected.acquired_payables
    selected["net_cash_consideration"] = selected.consideration - selected.acquired_cash

    def total(field):
        return selected[field].sum(min_count=len(selected))

    cash = total("net_cash_consideration")
    residual = financial.acquisitions - cash
    complete = pd.notna(residual) and abs(residual) <= 0.05
    acquired_nwc = total("acquired_nwc_proxy")
    nwc_remaining = financial.change_nwc_proxy - acquired_nwc if complete else float("nan")
    bridge = pd.DataFrame([
        ("Reported acquisition cash outflow", financial.acquisitions),
        ("Mapped consideration less cash acquired", cash),
        ("Unexplained acquisition cash difference", residual),
        ("Reported change in NWC proxy", financial.change_nwc_proxy),
        ("Mapped acquired NWC proxy", acquired_nwc),
        ("NWC change excluding mapped acquired balances (diagnostic)", nwc_remaining),
    ], columns=["Metric", "USD millions"]).set_index("Metric")
    bridge["Review"] = ["Cash-flow statement", "Acquisition disclosure",
                        "Ties at disclosed precision" if complete else "Incomplete mapping: do not use as total acquisition adjustment",
                        "Includes acquired balances", "Purchase-price allocation; see filing status",
                        "Capital payables and other accounts still need review" if complete else "Unavailable until acquisition cash bridge ties"]

    # Match full-year duration; no positional indexing across unrelated contexts.
    ga_facts = facts[facts.metric.eq("reported_ga") & facts.period_end.fillna("").str.startswith(f"{latest}-")]
    durations = (pd.to_datetime(ga_facts.period_end) - pd.to_datetime(ga_facts.period_start)).dt.days
    ga_facts = ga_facts[durations.between(330, 380)]
    ga = float(ga_facts.iloc[0].value) if len(ga_facts) == 1 else float("nan")
    candidate = total("ga_transaction_cost")
    volume = operating.reindex([latest]).get("annual_mmboe", pd.Series(float("nan"), index=[latest])).iloc[0]
    if pd.isna(volume) or volume <= 0:
        volume = float("nan")
    expenses = pd.DataFrame([
        ("G&A (USD millions)", ga, ga - candidate),
        ("G&A / Boe (USD)", ga / volume, (ga - candidate) / volume),
        ("EBIT (USD millions)", financial.ebit, financial.ebit + candidate),
        ("Calculated EBITDA (USD millions)", financial.ebitda, financial.ebitda + candidate),
    ], columns=["Metric", "Reported basis", "Candidate: remove disclosed G&A transaction costs"]).set_index("Metric")
    # Each deal's pro forma disclosure remains separate; pro formas are not additive.
    proforma = facts[facts.metric.isin(["proforma_revenue", "proforma_net_income", "acquiree_revenue_since_close", "acquiree_net_income_since_close"])
                     & facts.deal.isin(selected.deal)].copy()
    return {"deals": selected, "nwc": bridge, "expense_candidates": expenses,
            "proforma": proforma[["deal", "metric", "period_start", "period_end", "value", "units", "source"]]}
