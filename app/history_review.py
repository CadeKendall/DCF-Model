"""Accounting checks and descriptive operating reviews of annual history."""

import math

import pandas as pd


def accounting_checks(history, tolerance=0.05):
    """Return per-year residuals in USD millions; missing inputs never pass.

    A difference calls for source review, not an automatic correction. For
    example, discontinued operations or noncontrolling interests may explain
    differences in the simplified income and equity bridges. Ties establish
    internal consistency, not equivalence to company filing definitions.
    """
    if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Tolerance must be a finite nonnegative amount in USD millions.")
    definitions = [
        ("Balance sheet", "Assets - liabilities - equity", ["assets", "liabilities", "equity"],
         lambda row: row["assets"] - row["liabilities"] - row["equity"]),
        ("Net income", "Pretax income - income tax - net income", ["pretax_income", "income_tax", "net_income"],
         lambda row: row["pretax_income"] - row["income_tax"] - row["net_income"]),
    ]
    if history.provider.upper() == "WRDS":
        definitions.append((
            "Compustat operating income", "Revenue - expenses before D&A - D&A - EBIT",
            ["revenue", "operating_expenses_before_da", "dd_a", "ebit"],
            lambda row: row["revenue"] - row["operating_expenses_before_da"] - row["dd_a"] - row["ebit"],
        ))
    elif history.provider.upper() == "SEC":
        definitions.append((
            "Filed operating income", "Revenue - operating expenses - EBIT",
            ["revenue", "operating_expenses", "ebit"],
            lambda row: row["revenue"] - row["operating_expenses"] - row["ebit"],
        ))
    rows = []
    for year, annual_row in history.annual.iterrows():
        for name, formula, inputs, calculate in definitions:
            missing = [field for field in inputs if pd.isna(annual_row.get(field))]
            residual = float("nan") if missing else float(calculate(annual_row))
            status = "Missing input" if missing else "Ties" if abs(residual) <= tolerance else "Review difference"
            rows.append({
                "Fiscal year": year, "Check": name, "Formula": formula,
                "Residual (USD millions)": residual, "Tolerance (USD millions)": tolerance,
                "Status": status, "Missing fields": ", ".join(missing),
            })
    return pd.DataFrame(rows)


def operating_driver_review(annual, operating):
    """Compare the latest selected year with its preceding calendar year.

    Join by fiscal year and keep missing data missing. The selected unit-cost
    subtotal is only LOE + GP&T + G&A, not the company's entire cost structure.
    This function neither attributes growth to acquisitions nor forecasts it.
    """
    if annual.empty or not annual.index.is_unique or not operating.index.is_unique:
        raise ValueError("Financial history must be nonempty and both year indexes must be unique.")
    latest = int(annual.index.max())
    periods = [latest - 1, latest]
    financial = annual.reindex(periods)
    operational = operating.reindex(periods)

    def column(frame, name):
        return frame[name] if name in frame else pd.Series(float("nan"), index=periods)

    def divide(numerator, denominator):
        return numerator / denominator.replace(0, float("nan"))

    volume = column(operational, "annual_mmboe")
    daily_volume = column(operational, "total_mboe_day")
    oil = column(operational, "oil_mbbl_day")
    gas = column(operational, "gas_mmcf_day")
    cost_components = pd.concat([column(operational, key) for key in ("loe_boe", "gpt_boe", "ga_boe")], axis=1)
    specs = [
        ("Annual production", "MMBoe", volume),
        ("Oil production", "MBbl/day", oil),
        ("Gas production", "MMcf/day", gas),
        ("NGL production", "MBbl/day", column(operational, "ngl_mbbl_day")),
        ("Realized oil price", "USD/Bbl", column(operational, "realized_oil")),
        ("Realized gas price", "USD/Mcf", column(operational, "realized_gas")),
        ("Realized NGL price", "USD/Bbl", column(operational, "realized_ngl")),
        ("Oil share of production", "%", 100 * divide(oil, daily_volume)),
        ("Gas share of production", "%", 100 * divide(gas / 6, daily_volume)),
        ("Lease and well cost", "USD/Boe", column(operational, "loe_boe")),
        ("GP&T cost", "USD/Boe", column(operational, "gpt_boe")),
        ("G&A cost", "USD/Boe", column(operational, "ga_boe")),
        ("Selected unit-cost subtotal", "USD/Boe", cost_components.sum(axis=1, min_count=3)),
        ("Capex per Boe", "USD/Boe", divide(column(financial, "capex"), volume)),
        ("Calculated EBITDA per Boe", "USD/Boe", divide(column(financial, "ebitda"), volume)),
        ("Operating margin", "%", 100 * divide(column(financial, "ebit"), column(financial, "revenue"))),
        ("Capital expenditures", "USD millions", column(financial, "capex")),
        ("Operating cash flow", "USD millions", column(financial, "operating_cash_flow")),
    ]
    rows = []
    for metric, unit, series in specs:
        prior, current = series.loc[periods[0]], series.loc[periods[1]]
        growth = float("nan") if unit == "%" or pd.isna(prior) or prior == 0 else 100 * (current / prior - 1)
        rows.append({"Metric": metric, "Units": unit, str(periods[0]): prior,
                     str(periods[1]): current, "Change": current - prior,
                     "Change unit": "percentage points" if unit == "%" else unit,
                     "Change (%)": growth})
    return pd.DataFrame(rows).set_index("Metric")
