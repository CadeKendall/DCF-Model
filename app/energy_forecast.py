"""Upstream operating forecast and unlevered free cash flow, USD millions.

Revenue by commodity: daily volume x days / 1000 x realized price (the same unit
logic as the historical reconstruction in app.energy_revenue). Gas is priced
separately; 6 Mcf/Boe is used only for per-Boe cost intensity.

EBIT = operating income (net marketing margin, no derivative or disposition gains).
EBITDA = EBIT + DD&A (retains exploration and recurring impairments).
UFCF = EBIT x (1 - t) + DD&A + recurring impairments + deferred-tax share
       - capex (excluding capitalized interest and dry-hole cost already expensed)
       - change in operating NWC - asset-retirement settlements.
"""

import math
from datetime import date

import pandas as pd

from app.forecast_assumptions import days_in_year

LINE_ORDER = [
    ("Oil volume (MMBbl)", "oil_volume"), ("NGL volume (MMBbl)", "ngl_volume"),
    ("U.S. gas volume (Bcf)", "us_gas_volume"), ("International gas volume (Bcf)", "intl_gas_volume"),
    ("Production (MMBoe, 6:1)", "mmboe"),
    ("WTI (USD/Bbl)", "wti"), ("Henry Hub (USD/Mcf)", "henry_hub"),
    ("Realized oil (USD/Bbl)", "realized_oil"), ("Realized NGL (USD/Bbl)", "realized_ngl"),
    ("Realized U.S. gas (USD/Mcf)", "realized_us_gas"), ("Realized international gas (USD/Mcf)", "realized_intl_gas"),
    ("Oil sales", "oil_sales"), ("NGL sales", "ngl_sales"), ("U.S. gas sales", "us_gas_sales"),
    ("International gas sales", "intl_gas_sales"), ("Upstream sales", "upstream_sales"),
    ("Marketing margin, net", "marketing_margin"), ("Other revenue, net", "other_revenue"),
    ("Derivative and disposition gains (not forecast)", "nonrecurring_revenue"),
    ("Revenue, net of marketing costs", "revenue"),
    ("Lease and well", "lease_well"), ("Gathering, processing and transportation", "gpt"),
    ("General and administrative", "ga"), ("Taxes other than income", "toti"),
    ("Exploration", "exploration"), ("Dry-hole costs", "dry_hole"), ("Impairments", "impairments"),
    ("DD&A", "dd_a"), ("Operating expenses excl. marketing costs", "operating_expenses"),
    ("EBITDA (operating income + DD&A)", "ebitda"), ("EBIT (operating income)", "ebit"),
    ("Operating taxes on EBIT", "operating_taxes"), ("NOPAT", "nopat"),
    ("+ DD&A", "dd_a"), ("+ Recurring impairments (noncash)", "impairments"),
    ("+ Deferred share of operating taxes", "deferred_tax"),
    ("Capital expenditures (guidance basis)", "capex"), ("- Capitalized interest", "capitalized_interest"),
    ("- Dry-hole cost already in EBIT", "dry_hole"), ("Capex for UFCF", "capex_ufcf"),
    ("Operating NWC, end of period", "nwc_end"), ("Change in operating NWC", "change_nwc"),
    ("Asset-retirement settlements", "aro"), ("Unlevered free cash flow", "ufcf"),
]


def _period(*, days, volumes, prices, rates, fixed, tax_rate, current_share, nwc_begin, nwc_pct, annualize):
    """One period's operating model. ``fixed`` amounts are already for this period."""
    oil, ngl, us_gas, intl_gas = (volumes[k] for k in ("oil", "ngl", "us_gas", "intl_gas"))
    for name, v in volumes.items():
        if v < 0:
            raise ValueError(f"Negative {name} volume.")
    r = {}
    r["oil_volume"] = oil * days / 1000
    r["ngl_volume"] = ngl * days / 1000
    r["us_gas_volume"] = us_gas * days / 1000
    r["intl_gas_volume"] = intl_gas * days / 1000
    r["mmboe"] = r["oil_volume"] + r["ngl_volume"] + (r["us_gas_volume"] + r["intl_gas_volume"]) / 6
    r["wti"], r["henry_hub"] = prices["wti"], prices["henry_hub"]
    r["realized_oil"] = prices["wti"] + prices["oil_differential"]
    r["realized_ngl"] = prices["wti"] * prices["ngl_pct_wti"] / 100
    r["realized_us_gas"] = prices["henry_hub"] + prices["us_gas_differential"]
    r["realized_intl_gas"] = prices["intl_gas_price"]
    r["oil_sales"] = r["oil_volume"] * r["realized_oil"]
    r["ngl_sales"] = r["ngl_volume"] * r["realized_ngl"]
    r["us_gas_sales"] = r["us_gas_volume"] * r["realized_us_gas"]
    r["intl_gas_sales"] = r["intl_gas_volume"] * r["realized_intl_gas"]
    r["upstream_sales"] = r["oil_sales"] + r["ngl_sales"] + r["us_gas_sales"] + r["intl_gas_sales"]
    r["marketing_margin"] = fixed["marketing_margin"]
    r["other_revenue"] = fixed["other_revenue"]
    r["nonrecurring_revenue"] = 0.0
    r["revenue"] = r["upstream_sales"] + r["marketing_margin"] + r["other_revenue"]
    r["lease_well"] = rates["lease_well_boe"] * r["mmboe"]
    r["gpt"] = rates["gpt_boe"] * r["mmboe"]
    r["ga"] = rates["ga_boe"] * r["mmboe"]
    r["toti"] = rates["toti_pct"] / 100 * r["upstream_sales"]
    r["exploration"] = fixed["exploration"]
    r["dry_hole"] = fixed["dry_hole"]
    r["impairments"] = fixed["impairments"]
    r["dd_a"] = rates["dd_a_boe"] * r["mmboe"]
    r["operating_expenses"] = sum(r[k] for k in ("lease_well", "gpt", "ga", "toti", "exploration", "dry_hole", "impairments", "dd_a"))
    r["ebit"] = r["revenue"] - r["operating_expenses"]
    r["ebitda"] = r["ebit"] + r["dd_a"]
    r["operating_taxes"] = r["ebit"] * tax_rate / 100
    r["nopat"] = r["ebit"] - r["operating_taxes"]
    r["deferred_tax"] = r["operating_taxes"] * (1 - current_share / 100)
    r["capex"] = fixed["capex"]
    r["capitalized_interest"] = fixed["capitalized_interest"]
    r["capex_ufcf"] = r["capex"] - r["capitalized_interest"] - r["dry_hole"]
    r["nwc_end"] = nwc_pct / 100 * r["upstream_sales"] * annualize
    r["change_nwc"] = r["nwc_end"] - nwc_begin
    r["aro"] = fixed["aro"]
    r["ufcf"] = (r["nopat"] + r["dd_a"] + r["impairments"] + r["deferred_tax"]
                 - r["capex_ufcf"] - r["change_nwc"] - r["aro"])
    return r


def build_forecast(values, *, base_year, stub_quarter, years, nine_month, balance, scenario=None):
    """Return (statements DataFrame [line x period], periods dict, warnings).

    ``values``: resolved drivers from app.forecast_assumptions (driver -> {period: value}).
    ``nine_month``: reported year-to-date Series (app.eog_quarterly flows row).
    ``balance``: latest quarter-end balances Series (receivables, inventory, accounts_payable).
    ``scenario``: optional deltas (wti_delta, henry_hub_delta, volume_growth_delta, cost_inflation_delta).
    """
    scenario = scenario or {}
    if stub_quarter != 4:
        raise ValueError("This build supports a fourth-quarter stub (nine months reported).")
    warnings = []
    v = values
    q_days = (date(base_year + 1, 1, 1) - date(base_year, 10, 1)).days
    dry_share = nine_month["dry_hole"] / (nine_month["exploration"] + nine_month["dry_hole"])
    nwc_open = balance["receivables"] + balance["inventory"] - balance["accounts_payable"]

    def costs(period, inflation_factor):
        return {"lease_well_boe": v["lease_well_boe"][period] * inflation_factor,
                "gpt_boe": v["gpt_boe"][period] * inflation_factor,
                "ga_boe": v["ga_boe"][period] * inflation_factor,
                "dd_a_boe": v["dd_a_boe"][period], "toti_pct": v["toti_pct"][period]}

    def prices(period, shocked):
        return {"wti": v["wti"][period] + (scenario.get("wti_delta", 0.0) if shocked else 0.0),
                "henry_hub": v["henry_hub"][period] + (scenario.get("henry_hub_delta", 0.0) if shocked else 0.0),
                "oil_differential": v["oil_differential"][period], "ngl_pct_wti": v["ngl_pct_wti"][period],
                "us_gas_differential": v["us_gas_differential"][period], "intl_gas_price": v["intl_gas_price"][period]}

    periods = {}
    stub_volumes = {"oil": v["oil_growth"]["stub"], "ngl": v["ngl_growth"]["stub"],
                    "us_gas": v["us_gas_growth"]["stub"], "intl_gas": v["intl_gas_growth"]["stub"]}
    expl_dry = v["exploration_dry_hole"]["stub"]
    periods[f"{base_year}Q{stub_quarter}E"] = _period(
        days=q_days, volumes=stub_volumes, prices=prices("stub", False), rates=costs("stub", 1.0),
        fixed={"marketing_margin": v["marketing_margin"]["stub"], "other_revenue": v["other_revenue"]["stub"],
               "exploration": expl_dry * (1 - dry_share), "dry_hole": expl_dry * dry_share,
               "impairments": v["impairments"]["stub"], "capex": v["capex_per_boe"]["stub"],
               "capitalized_interest": v["capitalized_interest"]["stub"], "aro": v["aro_settlements"]["stub"]},
        tax_rate=v["operating_tax_rate"]["stub"], current_share=v["current_tax_share"]["stub"],
        nwc_begin=nwc_open, nwc_pct=v["nwc_pct_sales"]["stub"], annualize=days_in_year(base_year) / q_days)

    volumes = dict(stub_volumes)
    inflation = 1.0
    nwc_begin = periods[f"{base_year}Q{stub_quarter}E"]["nwc_end"]
    for year in years:
        growth_shift = scenario.get("volume_growth_delta", 0.0)
        volumes = {"oil": volumes["oil"] * (1 + (v["oil_growth"][year] + growth_shift) / 100),
                   "ngl": volumes["ngl"] * (1 + (v["ngl_growth"][year] + growth_shift) / 100),
                   "us_gas": volumes["us_gas"] * (1 + (v["us_gas_growth"][year] + growth_shift) / 100),
                   "intl_gas": volumes["intl_gas"] * (1 + v["intl_gas_growth"][year] / 100)}
        inflation *= 1 + (v["cost_inflation"][year] + scenario.get("cost_inflation_delta", 0.0)) / 100
        days = days_in_year(year)
        mmboe = (volumes["oil"] + volumes["ngl"] + (volumes["us_gas"] + volumes["intl_gas"]) / 6) * days / 1000
        expl_dry = v["exploration_dry_hole"][year]
        result = _period(
            days=days, volumes=volumes, prices=prices(year, True), rates=costs(year, inflation),
            fixed={"marketing_margin": v["marketing_margin"][year], "other_revenue": v["other_revenue"][year],
                   "exploration": expl_dry * (1 - dry_share), "dry_hole": expl_dry * dry_share,
                   "impairments": v["impairments"][year],
                   "capex": v["capex_per_boe"][year] * inflation * mmboe,
                   "capitalized_interest": v["capitalized_interest"][year], "aro": v["aro_settlements"][year]},
            tax_rate=v["operating_tax_rate"][year], current_share=v["current_tax_share"][year],
            nwc_begin=nwc_begin, nwc_pct=v["nwc_pct_sales"][year], annualize=1.0)
        nwc_begin = result["nwc_end"]
        periods[f"FY{year}E"] = result
        if result["ebit"] < 0:
            warnings.append(f"FY{year}E EBIT is negative; operating taxes are negative (assumes the loss is usable).")
    last = periods[f"FY{years[-1]}E"]
    if last["capex_ufcf"] < last["dd_a"]:
        warnings.append("Terminal-year capex is below DD&A: check that reinvestment sustains the volumes.")
    for name, r in periods.items():
        if any(not math.isfinite(x) for x in r.values()):
            raise ValueError(f"Non-finite value in {name}.")
    statements = pd.DataFrame({name: [r[key] for _, key in LINE_ORDER] for name, r in periods.items()},
                              index=[label for label, _ in LINE_ORDER])
    return statements, periods, warnings


def fy_base_bridge(nine_month, stub, *, base_year, guidance_ranges):
    """FY base-year estimate = nine months reported + Q4 forecast, with guidance checks."""
    ytd_days = (date(base_year, 10, 1) - date(base_year, 1, 1)).days
    q_days = (date(base_year + 1, 1, 1) - date(base_year, 10, 1)).days
    year_days = ytd_days + q_days
    rows = {}

    def daily(metric_ytd, stub_volume_key):
        return (nine_month[metric_ytd] * ytd_days + stub[stub_volume_key] * 1000) / year_days

    rows["Oil (MBbl/day)"] = (nine_month["oil_mbbl_day"], stub["oil_volume"] * 1000 / q_days, daily("oil_mbbl_day", "oil_volume"), "oil_mbbl_day")
    rows["NGL (MBbl/day)"] = (nine_month["ngl_mbbl_day"], stub["ngl_volume"] * 1000 / q_days, daily("ngl_mbbl_day", "ngl_volume"), "ngl_mbbl_day")
    gas_q4 = (stub["us_gas_volume"] + stub["intl_gas_volume"]) * 1000 / q_days
    rows["Natural gas (MMcf/day)"] = (nine_month["gas_mmcf_day"], gas_q4,
                                      (nine_month["gas_mmcf_day"] * ytd_days + gas_q4 * q_days) / year_days, "gas_mmcf_day")
    boe_q4 = stub["mmboe"] * 1000 / q_days
    rows["Total (MBoe/day)"] = (nine_month["total_mboe_day"], boe_q4,
                                (nine_month["total_mboe_day"] * ytd_days + boe_q4 * q_days) / year_days, "total_mboe_day")
    rows["Capex, guidance basis (USD millions)"] = (nine_month["capex_nongaap"], stub["capex"],
                                                   nine_month["capex_nongaap"] + stub["capex"], "capex_nongaap")
    table = []
    for label, (ytd, q4, fy, metric) in rows.items():
        low, high = guidance_ranges.get(metric, (None, None))
        status = "No range" if low is None else ("Inside FY guidance" if low <= fy <= high else "Outside FY guidance")
        table.append({"Item": label, f"9M {base_year} reported": ytd, f"Q4 {base_year}E": q4,
                      f"FY{base_year}E": fy, "FY guidance low": low, "FY guidance high": high, "Status": status})
    return pd.DataFrame(table).set_index("Item")
