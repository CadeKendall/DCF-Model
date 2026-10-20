"""Historical operating-cost bridge, separate from recurring forecast assumptions."""

from datetime import date
import math

import pandas as pd

EXPENSES = {
    "lease_well": "Lease and well",
    "gpt": "Gathering, processing and transportation",
    "exploration": "Exploration",
    "dry_hole": "Dry-hole costs",
    "impairments": "Impairments",
    "marketing_costs": "Marketing costs",
    "ga": "General and administrative",
    "taxes_other_than_income": "Taxes other than income",
    "dd_a": "Depreciation, depletion and amortization",
}


def build_cost_bridge(disclosures, annual, *, provider, tolerance=0.05):
    """Return cost bridge, unit-cost diagnostics and explicit reconciliation.

    EBIT here is operating income. EBITDA = operating income + DD&A; it retains
    impairments, noncash compensation and revenue-side gains/losses. Neither
    measure is adjusted EBITDA or operating/free cash flow.
    """
    if provider.upper() != "SEC":
        raise ValueError("Use SEC history for the filing-based operating-cost bridge.")
    if not annual.index.is_unique or not disclosures.index.is_unique or annual.empty:
        raise ValueError("Use nonempty annual history and unique year indexes.")
    if isinstance(tolerance, bool) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Tolerance must be finite and nonnegative, in USD millions.")
    data = disclosures.reindex(annual.index)

    def column(field):
        return data[field] if field in data else pd.Series(float("nan"), index=annual.index)

    expenses = pd.DataFrame({label: column(field) for field, label in EXPENSES.items()})
    non_da = expenses.drop(columns=[EXPENSES["dd_a"]]).sum(axis=1, min_count=len(EXPENSES) - 1)
    total = expenses.sum(axis=1, min_count=len(EXPENSES))
    ebit = column("reported_revenue") - total
    ebitda = ebit + column("dd_a")
    bridge = pd.DataFrame({"Reported revenue": column("reported_revenue")})
    bridge = pd.concat([bridge, expenses], axis=1)
    bridge["Sum of operating expense lines"] = total
    bridge["Reported operating expenses"] = column("reported_operating_expenses")
    bridge["Operating expenses excluding DD&A"] = non_da
    bridge["Calculated EBITDA (operating-income basis)"] = ebitda
    bridge["Calculated EBIT (operating income)"] = ebit
    bridge["Reported operating income"] = column("reported_operating_income")
    bridge["Reported operating income + DD&A"] = column("reported_operating_income") + column("dd_a")

    # Full calendar-year average output; gas conversion is an energy equivalence,
    # used for cost intensity only. Revenue continues to price each commodity.
    days = pd.Series({year: (date(int(year) + 1, 1, 1) - date(int(year), 1, 1)).days for year in annual.index})
    volume = (column("oil_mbbl_day") + column("ngl_mbbl_day") + column("gas_mmcf_day") / 6) * days / 1000
    denominator = volume.where(volume > 0)
    upstream_sales = pd.concat([column(x) for x in ("oil_sales", "gas_sales", "ngl_sales")], axis=1).sum(axis=1, min_count=3)
    unit_costs = pd.DataFrame({"Calculated production (MMBoe)": volume})
    for field in ("lease_well", "gpt", "ga", "dd_a", "exploration", "dry_hole", "impairments"):
        unit_costs[f"{EXPENSES[field]} / Boe (USD)"] = column(field) / denominator
    unit_costs["Taxes other than income / upstream sales (%)"] = 100 * column("taxes_other_than_income") / upstream_sales.where(upstream_sales != 0)
    unit_costs["Operating margin (%)"] = 100 * ebit / column("reported_revenue").where(column("reported_revenue") != 0)

    checks = {
        "Expense sum less reported expenses": total - column("reported_operating_expenses"),
        "Calculated EBIT less reported operating income": ebit - column("reported_operating_income"),
        "Calculated EBITDA less (reported operating income + DD&A)": ebitda - (column("reported_operating_income") + column("dd_a")),
    }
    for filed, historical, label in [
        ("reported_revenue", "revenue", "Filing revenue less SEC annual history"),
        ("reported_operating_expenses", "operating_expenses", "Filing expenses less SEC annual history"),
        ("reported_operating_income", "ebit", "Filing operating income less SEC annual history"),
        ("dd_a", "dd_a", "Filing DD&A less SEC annual history"),
    ]:
        historical_series = annual[historical] if historical in annual else pd.Series(float("nan"), index=annual.index)
        checks[label] = column(filed) - historical_series
    reconciliation = pd.DataFrame([
        {"Year": year, "Check": label, "Residual (USD millions)": series.loc[year],
         "Status": "Missing input" if pd.isna(series.loc[year]) else
         "Ties" if abs(series.loc[year]) <= tolerance else "Review difference"}
        for label, series in checks.items() for year in annual.index
    ])
    return bridge, unit_costs, reconciliation
