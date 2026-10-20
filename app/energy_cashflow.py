"""Historical cash-flow reconciliation and reinvestment diagnostics, USD millions.

These outputs explain reported cash flows. They do not select an operating tax
rate, approve a DCF NWC definition, or label CFO minus capex as unlevered FCF.
"""

import math

import pandas as pd

# Tag values are not always signed cash-flow contributions. In particular,
# positive increases in operating assets consume cash and gains are reversed.
CFO_COMPONENTS = [
    ("net_income", 1, "Net income"),
    ("dd_a", 1, "DD&A addback"),
    ("impairments", 1, "Impairments addback"),
    ("stock_compensation", 1, "Stock compensation addback"),
    ("deferred_tax", 1, "Deferred tax adjustment"),
    ("disposition_gain_loss", -1, "Reverse asset-disposition gain / loss"),
    ("other_noncash_income", -1, "Other noncash adjustment"),
    ("dry_hole", 1, "Dry-hole cost adjustment"),
    ("derivative_statement_gain_loss", -1, "Reverse derivative gain / loss"),
    ("derivative_cash_settlements", 1, "Derivative cash settlements"),
    ("other_operating_adjustment", 1, "Other operating adjustment"),
    ("increase_receivables", -1, "Receivables cash contribution"),
    ("increase_inventory", -1, "Inventory cash contribution"),
    ("increase_payables", 1, "Payables cash contribution"),
    ("increase_tax_payables", 1, "Accrued taxes payable cash contribution"),
    ("increase_other_assets", -1, "Other assets cash contribution"),
    ("increase_other_liabilities", 1, "Other liabilities cash contribution"),
    ("investing_wc_in_cfo", 1, "Investing-related WC reclassification in CFO"),
]


def build_cashflow_review(disclosures, annual, *, provider, tolerance=0.05):
    if provider.upper() != "SEC":
        raise ValueError("Use SEC annual history for cash-flow reconciliation.")
    if annual.empty or not annual.index.is_unique or not disclosures.index.is_unique:
        raise ValueError("Use nonempty annual history and unique fiscal-year indexes.")
    if isinstance(tolerance, bool) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Tolerance must be finite and nonnegative in USD millions.")
    data = disclosures.reindex(annual.index)

    def col(field):
        return data[field] if field in data else pd.Series(float("nan"), index=annual.index)

    def historical(field):
        return annual[field] if field in annual else pd.Series(float("nan"), index=annual.index)

    def complete_sum(*series):
        return pd.concat(series, axis=1).sum(axis=1, min_count=len(series))

    cfo = pd.DataFrame({label: sign * col(field) for field, sign, label in CFO_COMPONENTS})
    cfo["Rebuilt operating cash flow"] = cfo.sum(axis=1, min_count=len(CFO_COMPONENTS))
    cfo["Reported operating cash flow"] = col("operating_cash_flow")
    cfo["Rebuilt less reported CFO"] = cfo["Rebuilt operating cash flow"] - col("operating_cash_flow")

    pretax = col("pretax_income").where(col("pretax_income") > 0)
    taxes = pd.DataFrame({
        "Pretax income": col("pretax_income"),
        "Current tax provision": col("current_tax"),
        "Deferred tax provision / benefit": col("deferred_tax"),
        "Other tax provision / benefit": col("other_tax"),
        "Sum of tax provision components": complete_sum(col("current_tax"), col("deferred_tax"), col("other_tax")),
        "Reported income tax expense": col("income_tax"),
        "Cash income taxes paid, net of refunds": col("cash_taxes_paid"),
        "Tax-credit purchases (memo; already included in disclosed cash taxes)": col("tax_credit_purchases_memo"),
        "Cash taxes less current provision (timing/scope diagnostic)": col("cash_taxes_paid") - col("current_tax"),
        "Effective book tax rate (%)": 100 * col("income_tax") / pretax,
        "Cash taxes / pretax income (%) — diagnostic only": 100 * col("cash_taxes_paid") / pretax,
    })

    additions = complete_sum(col("oil_gas_additions"), col("other_ppe_additions"))
    # Explicit view of the property-addition lines plus the separately presented
    # investing WC movement. This does not classify growth/maintenance spending.
    spending = additions - col("investing_wc_in_cfi")
    cfi_before_acquisitions = -spending + col("asset_sale_proceeds")
    investing = pd.DataFrame({
        "Oil and gas property additions": col("oil_gas_additions"),
        "Other PP&E additions": col("other_ppe_additions"),
        "Property additions before investing WC": additions,
        "Investing WC contribution (inflow positive)": col("investing_wc_in_cfi"),
        "Property additions less investing WC inflow": spending,
        "Business acquisitions, net of acquired cash": col("acquisitions"),
        "Asset-sale cash proceeds": col("asset_sale_proceeds"),
        "Other investing payments (reported where available)": col("other_investing_payments"),
        "CFI from additions, investing WC and asset sales": cfi_before_acquisitions,
        "Reported investing cash flow": col("investing_cash_flow"),
        "Remaining CFI: acquisitions and other investing": col("investing_cash_flow") - cfi_before_acquisitions,
        "Rebuilt CFI from all disclosed statement lines": col("reconstructed_cfi"),
        "Rebuilt less reported CFI": col("reconstructed_cfi") - col("investing_cash_flow"),
        "Capitalized interest (memo; do not add to spending again)": col("capitalized_interest"),
        "Cash interest net of capitalized interest (memo)": col("cash_interest_net_capitalized"),
        "Reported CFO": col("operating_cash_flow"),
        "CFO less property additions before investing WC": col("operating_cash_flow") - additions,
        "CFO less property additions after investing WC": col("operating_cash_flow") - spending,
    })

    ar = -col("increase_receivables")
    inventory = -col("increase_inventory")
    ap = col("increase_payables")
    narrow = complete_sum(ar, inventory, ap)
    broad = complete_sum(narrow, col("increase_tax_payables"), -col("increase_other_assets"), col("increase_other_liabilities"))
    working_capital = pd.DataFrame({
        "Balance-sheet NWC proxy change (investment positive)": historical("change_nwc_proxy"),
        "Cash-flow receivables contribution": ar,
        "Cash-flow inventory contribution": inventory,
        "Cash-flow payables contribution": ap,
        "Narrow three-account cash contribution": narrow,
        "Negative narrow contribution (investment-positive diagnostic)": -narrow,
        "Accrued tax payable contribution": col("increase_tax_payables"),
        "Other assets contribution": -col("increase_other_assets"),
        "Other liabilities contribution": col("increase_other_liabilities"),
        "Six-account cash contribution": broad,
        "Investing WC reclassification in CFO": col("investing_wc_in_cfo"),
        "Six-account contribution plus reclassification": broad + col("investing_wc_in_cfo"),
    })
    checks = {
        "Net income to CFO reconciliation": cfo["Rebuilt less reported CFO"],
        "Tax provision components less reported expense": taxes["Sum of tax provision components"] - col("income_tax"),
        "CFO and CFI investing-WC entries offset": col("investing_wc_in_cfo") + col("investing_wc_in_cfi"),
        "All disclosed investing lines reconcile to CFI": investing["Rebuilt less reported CFI"],
        "Property additions less existing capex field": additions - historical("capex"),
    }
    for field in ("net_income", "dd_a", "pretax_income", "income_tax", "operating_cash_flow", "acquisitions"):
        checks[f"Filing {field} less SEC annual history"] = col(field) - historical(field)
    reconciliation = pd.DataFrame([
        {"Year": year, "Check": label, "Residual (USD millions)": series.loc[year],
         "Status": "Missing input" if pd.isna(series.loc[year]) else
         "Ties" if abs(series.loc[year]) <= tolerance else "Review difference"}
        for label, series in checks.items() for year in annual.index
    ])
    sign_guide = pd.DataFrame(CFO_COMPONENTS, columns=["Source metric", "Cash-flow multiplier", "Bridge line"])
    return {"cfo": cfo, "taxes": taxes, "investing": investing,
            "working_capital": working_capital, "checks": reconciliation, "sign_guide": sign_guide}
