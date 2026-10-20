"""EOG cash-flow and tax disclosures; raw XBRL signs remain in the source data."""

import xml.etree.ElementTree as ET

import pandas as pd

from app.eog_revenue import NS, load_revenue_history
from app.sec import PROJECT_ROOT, SECError
from app.sec_filings import load_annual_filing

CASHFLOW_TAGS = {
    ("us-gaap:NetIncomeLoss", ""): "net_income",
    ("us-gaap:DepreciationDepletionAndAmortization", ""): "dd_a",
    ("us-gaap:ImpairmentOfOilAndGasProperties", ""): "impairments",
    ("us-gaap:ShareBasedCompensation", ""): "stock_compensation",
    ("us-gaap:DeferredIncomeTaxExpenseBenefit", ""): "deferred_tax",
    ("us-gaap:OtherNoncashIncomeExpense", ""): "other_noncash_income",
    ("us-gaap:ResultsOfOperationsDryHoleCosts", ""): "dry_hole",
    ("us-gaap:GainLossOnDerivativeInstrumentsNetPretax", ""): "derivative_statement_gain_loss",
    ("eog:CashReceivedPaidOnDerivativeInstrumentsNet", ""): "derivative_cash_settlements",
    ("eog:OtherNetExcludingGainsLosses", ""): "other_operating_adjustment",
    ("us-gaap:IncreaseDecreaseInReceivables", ""): "increase_receivables",
    ("us-gaap:IncreaseDecreaseInInventories", ""): "increase_inventory",
    ("us-gaap:IncreaseDecreaseInAccountsPayable", ""): "increase_payables",
    ("us-gaap:IncreaseDecreaseInAccruedTaxesPayable", ""): "increase_tax_payables",
    ("us-gaap:IncreaseDecreaseInOtherOperatingAssets", ""): "increase_other_assets",
    ("us-gaap:IncreaseDecreaseInOtherOperatingLiabilities", ""): "increase_other_liabilities",
    ("eog:ChangesInComponentsOfWorkingCapitalAssociatedWithInvestingAndFinancingActivities", ""): "investing_wc_in_cfo",
    ("us-gaap:NetCashProvidedByUsedInOperatingActivities", ""): "operating_cash_flow",
    ("us-gaap:PaymentsToAcquireOilAndGasPropertyAndEquipment", ""): "oil_gas_additions",
    ("us-gaap:PaymentsToAcquireOtherPropertyPlantAndEquipment", ""): "other_ppe_additions",
    ("eog:ChangesInComponentsOfWorkingCapitalAssociatedWithInvestingActivities", ""): "investing_wc_in_cfi",
    ("us-gaap:PaymentsToAcquireBusinessesNetOfCashAcquired", ""): "acquisitions",
    ("us-gaap:ProceedsFromSaleOfOilAndGasPropertyAndEquipment", ""): "asset_sale_proceeds",
    ("us-gaap:NetCashProvidedByUsedInInvestingActivities", ""): "investing_cash_flow",
    ("us-gaap:PaymentsForProceedsFromOtherInvestingActivities", ""): "other_investing_payments",
    ("us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", ""): "pretax_income",
    ("us-gaap:IncomeTaxExpenseBenefit", ""): "income_tax",
    ("us-gaap:CurrentIncomeTaxExpenseBenefit", ""): "current_tax",
    ("us-gaap:OtherIncomeTaxExpenseBenefitContinuingOperations", ""): "other_tax",
    ("us-gaap:IncomeTaxesPaidNet", ""): "cash_taxes_paid",
    ("eog:PaymentsToAcquireEnergyRelatedTaxCredits", ""): "tax_credit_purchases_memo",
    ("us-gaap:InterestPaidNet", ""): "cash_interest_net_capitalized",
    ("us-gaap:InterestCostsCapitalizedAdjustment", ""): "capitalized_interest",
}

INVESTING_SIGNS = {"oil_gas_additions": -1, "other_ppe_additions": -1,
                   "investing_wc_in_cfi": 1, "acquisitions": -1,
                   "asset_sale_proceeds": 1, "other_investing_payments": -1}


def _investing_line_fields(document):
    """Require every tagged investing-statement line to have a known mapping.

    Rebuilding a statement from its actual disclosed lines does not populate
    absent categories with invented zeros. Unknown additional lines stop this
    reconciliation so a future filing cannot silently omit a cash flow.
    """
    root = ET.fromstring(document)
    tables = [t for t in root.findall(".//h:table", NS)
              if "Net Cash Provided by Operating Activities" in " ".join(t.itertext())
              and "Net Income" in " ".join(t.itertext())]
    if len(tables) != 1:
        raise SECError("Expected one EOG cash-flow statement to determine investing-line coverage.")
    active = False
    fields = []
    for row in tables[0].findall("./h:tr", NS):
        label = " ".join("".join(row.itertext()).split())
        if label == "Investing Cash Flows":
            active = True
            continue
        if not active:
            continue
        facts = row.findall(".//ix:nonFraction", NS)
        if not facts:
            continue
        tags = {fact.attrib.get("name") for fact in facts}
        if tags == {"us-gaap:NetCashProvidedByUsedInInvestingActivities"}:
            if not fields:
                raise SECError("Investing statement contains no mapped detail lines.")
            return fields
        if len(tags) != 1:
            raise SECError("Mixed concepts in investing-statement row; inspect source.")
        tag = next(iter(tags))
        field = CASHFLOW_TAGS.get((tag, ""))
        if field not in INVESTING_SIGNS or field in fields:
            raise SECError(f"Unsupported or duplicate investing-statement line: {tag}.")
        fields.append(field)
    raise SECError("Could not identify the complete investing-statement section.")


def load_cashflow_history(years, *, cik, ticker, refresh=False, root=PROJECT_ROOT):
    """Use the same source selection and audit as the revenue and cost sections.

    Values retain the tag's sign convention. The model layer explicitly changes
    asset increases and income reversals into cash-flow contributions. Missing
    acquisition or tax-credit disclosures remain missing, not inferred zeros.
    """
    data, _, audit = load_revenue_history(years, cik=cik, ticker=ticker, refresh=refresh,
                                        root=root, extra_financial_tags=CASHFLOW_TAGS)
    data["reconstructed_cfi"] = float("nan")
    derived = []
    for accession, source_rows in audit.groupby("accession"):
        first = source_rows.iloc[0]
        document, metadata, _ = load_annual_filing(cik, ticker, int(first.filing_report_date[:4]), root=root)
        if metadata["accession"] != accession:
            raise SECError("Filing selection changed during extraction; rerun with a consistent cache.")
        fields = _investing_line_fields(document)
        for year in source_rows.year.unique():
            amounts = data.loc[year, fields]
            contributions = pd.Series({field: amounts[field] * INVESTING_SIGNS[field] for field in fields})
            value = contributions.sum(min_count=len(fields))
            data.at[year, "reconstructed_cfi"] = value
            derived.append({"year": year, "metric": "reconstructed_cfi", "value": value,
                            "units": "USD millions", "provider": "Calculated from SEC",
                            "source_field": " + ".join(f"({INVESTING_SIGNS[field]} * {field})" for field in fields),
                            "source": metadata["url"], "accession": accession,
                            "period_start": f"{year}-01-01", "period_end": f"{year}-12-31",
                            "filed": metadata["filed"], "filing_report_date": metadata["report_date"],
                            "retrieved_at_utc": metadata["retrieved_at_utc"]})
    audit = pd.concat([audit, pd.DataFrame(derived)], ignore_index=True)
    return data, audit
