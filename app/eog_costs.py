"""EOG historical operating expenses, using the same filing selection as revenue."""

import pandas as pd

from app.eog_revenue import load_revenue_history
from app.sec import PROJECT_ROOT

# These are EOG's observed statement mappings. OperatingLeaseExpense labels the
# entire Lease and Well line here; it is not the separate ASC 842 lease-cost note.
COST_TAGS = {
    ("us-gaap:OperatingLeaseExpense", ""): "lease_well",
    ("eog:GatheringProcessingAndTransportationCosts", ""): "gpt",
    ("us-gaap:ResultsOfOperationsTransportationCosts", ""): "transportation",
    ("eog:GatheringAndProcessingCosts", ""): "gathering_processing",
    ("us-gaap:ExplorationExpense", ""): "exploration",
    ("us-gaap:ResultsOfOperationsDryHoleCosts", ""): "dry_hole",
    ("us-gaap:ImpairmentOfOilAndGasProperties", ""): "impairments",
    ("us-gaap:DepreciationDepletionAndAmortization", ""): "dd_a",
    ("us-gaap:GeneralAndAdministrativeExpense", ""): "ga",
    ("eog:TaxesOtherThanIncome", ""): "taxes_other_than_income",
    ("us-gaap:CostsAndExpenses", ""): "reported_operating_expenses",
    ("us-gaap:OperatingIncomeLoss", ""): "reported_operating_income",
}


def load_cost_history(years, *, cik, ticker, refresh=False, root=PROJECT_ROOT):
    """Retrieve annual costs and their revenue/production context without constants.

    Use reported combined GP&T when available; otherwise sum both disclosed
    components. Missing components never become zero. Preserve the derivation
    and source references in the returned audit table.
    """
    data, _, audit = load_revenue_history(years, cik=cik, ticker=ticker, refresh=refresh,
                                        root=root, extra_financial_tags=COST_TAGS)
    data = data.copy()
    derived = []
    for year in data.index:
        if pd.notna(data.at[year, "gpt"]):
            continue
        components = data.loc[year, ["transportation", "gathering_processing"]]
        if components.isna().any():
            continue
        data.at[year, "gpt"] = components.sum()
        sources = audit[audit.year.eq(year) & audit.metric.isin(components.index)]
        first = sources.iloc[0]
        derived.append({"year": year, "metric": "gpt", "value": components.sum(),
                        "units": "USD millions", "provider": "Calculated from SEC",
                        "source_field": "transportation + gathering_processing",
                        "source": " | ".join(sources.source.unique()),
                        **{key: first[key] for key in ["period_start", "period_end", "filed",
                                                       "accession", "filing_report_date", "retrieved_at_utc"]}})
    if derived:
        audit = pd.concat([audit, pd.DataFrame(derived)], ignore_index=True)
    return data, audit
