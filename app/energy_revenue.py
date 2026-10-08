"""Volume-price revenue calculations and historical reconciliation, USD millions."""

from datetime import date

import pandas as pd

COMMODITIES = {
    "Oil and condensate": ("oil_mbbl_day", "realized_oil", "oil_sales", "MBbl/day", "MMBbl", "USD/Bbl"),
    "Natural gas": ("gas_mmcf_day", "realized_gas", "gas_sales", "MMcf/day", "Bcf", "USD/Mcf"),
    "NGLs": ("ngl_mbbl_day", "realized_ngl", "ngl_sales", "MBbl/day", "MMBbl", "USD/Bbl"),
}


def build_revenue_bridge(disclosures, precision, annual, *, provider):
    """Calculate historical sales with no price or volume forecast assumptions.

    Both daily-volume units use /1000 for USD millions: MBbl->MMBbl for
    liquids, and MMcf * (1000 Mcf/MMcf) / 1e6 for gas. No Boe conversion.
    Rounding bounds assume nearest-unit rounding at the published precision;
    they are diagnostics, not evidence that all residuals arise from rounding.
    """
    if provider.upper() != "SEC":
        raise ValueError("Use SEC annual history for the filed revenue reconciliation.")
    if not disclosures.index.is_unique or not annual.index.is_unique or not precision.index.is_unique:
        raise ValueError("Revenue inputs must have unique fiscal-year indexes.")
    data = disclosures.reindex(annual.index)
    rounding = precision.reindex(annual.index)
    rows = []

    def value(frame, year, field):
        return frame.at[year, field] if field in frame else float("nan")

    for year in data.index:
        days = (date(int(year) + 1, 1, 1) - date(int(year), 1, 1)).days
        for commodity, (volume_field, price_field, sales_field, daily_unit, annual_unit, price_unit) in COMMODITIES.items():
            daily = value(data, year, volume_field)
            price = value(data, year, price_field)
            if pd.notna(daily) and daily < 0:
                raise ValueError(f"Negative production volume for {commodity} in {year}.")
            volume = daily * days / 1000
            sales = volume * price
            reported = value(data, year, sales_field)
            difference = sales - reported
            dq, dp, dr = [value(rounding, year, key) for key in (volume_field, price_field, sales_field)]
            bound = (abs(daily) * dp + abs(price) * dq + dq * dp) * days / 1000 + dr
            status = "Missing input" if pd.isna(difference) or pd.isna(bound) else (
                "Within rounding bound" if abs(difference) <= bound else "Review difference")
            rows.append({"Year": year, "Commodity": commodity, "Days": days,
                         "Daily volume": daily, "Daily unit": daily_unit,
                         "Annual volume (calculated)": volume, "Annual unit": annual_unit,
                         "Realized price": price, "Price unit": price_unit,
                         "Calculated sales": sales, "Reported sales": reported,
                         "Calculated less reported": difference, "Rounding bound": bound, "Status": status})
    commodity_table = pd.DataFrame(rows).set_index(["Year", "Commodity"])
    bridge = pd.DataFrame(index=annual.index)
    for commodity, (_, _, sales_field, _, _, _) in COMMODITIES.items():
        bridge[f"Reported {commodity} sales"] = data.get(sales_field, float("nan"))
    sales_columns = list(bridge.columns)
    bridge["Reported upstream sales"] = bridge[sales_columns].sum(axis=1, min_count=3)
    calculated = commodity_table["Calculated sales"].unstack("Commodity")
    bridge["Calculated upstream sales"] = calculated.sum(axis=1, min_count=3)
    bridge["Calculated less reported upstream"] = bridge["Calculated upstream sales"] - bridge["Reported upstream sales"]
    components = ["Reported upstream sales"]
    for field, label in [("marketing_revenue", "Gathering, processing and marketing revenue"),
                         ("derivative_gain_loss", "Derivative gain / loss"),
                         ("disposition_gain_loss", "Asset disposition gain / loss"),
                         ("other_revenue", "Other revenue, net")]:
        bridge[label] = data.get(field, float("nan"))
        components.append(label)
    bridge["Sum of reported revenue components"] = bridge[components].sum(axis=1, min_count=len(components))
    bridge["Reported total revenue in filing"] = data.get("reported_revenue", float("nan"))
    bridge["Component sum less filing total"] = bridge["Sum of reported revenue components"] - bridge["Reported total revenue in filing"]
    bridge["SEC annual history revenue"] = annual.revenue
    bridge["Filing total less SEC annual history"] = bridge["Reported total revenue in filing"] - bridge["SEC annual history revenue"]
    bridge["Marketing costs (shown separately)"] = data.get("marketing_costs", float("nan"))
    bridge["Marketing revenue less marketing costs"] = bridge["Gathering, processing and marketing revenue"] - bridge["Marketing costs (shown separately)"]
    # This subtotal excludes any costs classified elsewhere; it is not segment EBITDA.
    return commodity_table, bridge
