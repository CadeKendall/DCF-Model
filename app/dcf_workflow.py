"""Connect resolved assumptions, the forecast engine and the valuation engine.

Every scenario and sensitivity calls the same run_valuation() with different
inputs, so Bear/Base/Bull/Custom share one calculation path.
"""

import copy
from datetime import date

import pandas as pd

from app.energy_forecast import build_forecast
from app.valuation import (ValuationError, build_wacc, dcf, discount_schedule, equity_bridge,
                           sensitivity, terminal_ufcf)


def wacc_inputs(selections, *, values, years, treasury, erp, industry, profile, cover, balance,
                nine_month_shares, guidance_interest, prior_year_end_debt):
    """Resolve WACC rules into decimals, with a source table. Returns (inputs dict, table)."""
    w = selections["wacc"]
    debt = balance["debt_current"] + balance["debt_noncurrent"]
    dilution = nine_month_shares["diluted"] - nine_month_shares["basic"]
    diluted_shares = cover["shares"] + dilution
    tax = values["operating_tax_rate"][years[0]] / 100
    resolved, rows = {}, []

    def add(key, value, source, spec):
        resolved[key] = value
        rows.append({"input": key, "value": value, "source / as-of": source,
                     "rationale": spec.get("rationale", ""), "status": spec.get("status", "")})

    if w["risk_free"]["rule"] != "treasury_10y":
        raise ValuationError("Unsupported risk-free rule.")
    add("risk_free", treasury["value"], f"{treasury['source']}, {treasury['observation_date']}", w["risk_free"])
    rule = w["equity_risk_premium"]["rule"]
    if rule == "damodaran_erp_10y_average":
        add("equity_risk_premium", erp["ten_year_average"],
            f"Damodaran implied ERP, 10-year average to end-{erp['latest_year_end']} (latest {erp['latest']:.2%}); retrieved {erp['retrieved_at_utc'][:10]}",
            w["equity_risk_premium"])
    elif rule == "damodaran_erp_latest":
        add("equity_risk_premium", erp["latest"], f"Damodaran implied ERP end-{erp['latest_year_end']}", w["equity_risk_premium"])
    else:
        raise ValuationError("Unsupported ERP rule.")
    if w["beta"]["rule"] != "relevered_industry_beta":
        raise ValuationError("Unsupported beta rule.")
    add("unlevered_beta", industry["unlevered_beta_cash_corrected"],
        f"Damodaran {industry['industry']} ({industry['firms']} firms), cash-corrected unlevered beta; "
        f"FMP regression beta {profile['fmp_beta']} for reference", w["beta"])
    if w["pre_tax_cost_of_debt"]["rule"] != "interest_over_average_debt":
        raise ValuationError("Unsupported cost-of-debt rule.")
    gross_interest = guidance_interest["net_interest"] + guidance_interest["capitalized_interest"]
    average_debt = (debt + prior_year_end_debt) / 2
    add("pre_tax_cost_of_debt", gross_interest / average_debt,
        f"(FY guided net interest {guidance_interest['net_interest']:g} + capitalized {guidance_interest['capitalized_interest']:g}) "
        f"/ average debt {average_debt:,.0f}", w["pre_tax_cost_of_debt"])
    add("tax_rate", tax, "Operating tax rate from the assumptions table", w["tax_rate"])
    add("share_price", profile["price"], f"{profile['source']}, retrieved {profile['retrieved_at_utc'][:16]} UTC",
        {"status": "Retrieved market data"})
    add("diluted_shares", diluted_shares,
        f"Cover-page shares {cover['shares']:.1f}M as of {cover['as_of']} + Q3 dilutive shares {dilution:g}M",
        {"status": "Calculated from filings"})
    add("debt", debt, f"Current + long-term debt at {balance.name} (includes finance leases; excludes operating leases)",
        {"status": "Reported"})
    return resolved, pd.DataFrame(rows)


def run_valuation(values, selections, *, years, nine_month, balance, inputs, bridge_adjustments,
                  growth, exit_multiple, scenario=None, wacc_override=None):
    """Forecast + WACC + DCF + equity bridge for one set of inputs."""
    scenario = scenario or {}
    v = selections["valuation"]
    base_year, stub_quarter = int(v["base_year"]), int(v["stub_quarter"])
    statements, periods, warnings = build_forecast(values, base_year=base_year, stub_quarter=stub_quarter,
                                                   years=years, nine_month=nine_month, balance=balance,
                                                   scenario=scenario)
    wacc = build_wacc(risk_free=inputs["risk_free"], unlevered_beta=inputs["unlevered_beta"],
                      equity_risk_premium=inputs["equity_risk_premium"],
                      pre_tax_cost_of_debt=inputs["pre_tax_cost_of_debt"], tax_rate=inputs["tax_rate"],
                      share_price=inputs["share_price"], diluted_shares=inputs["diluted_shares"], debt=inputs["debt"])
    rate = wacc.wacc + scenario.get("wacc_delta", 0.0) / 100 if wacc_override is None else wacc_override
    valuation_date = date.fromisoformat(v["valuation_date"])
    stub_end = date(base_year + 1, 1, 1)
    q_start = date(base_year, 3 * stub_quarter - 2, 1)
    schedule, horizon = discount_schedule(valuation_date, stub_end, years, mid_year=v.get("mid_year_convention", True))
    stub_name = f"{base_year}Q{stub_quarter}E"
    stub_fraction = (stub_end - valuation_date).days / (stub_end - q_start).days
    flows = pd.Series({"stub": periods[stub_name]["ufcf"] * stub_fraction,
                       **{y: periods[f"FY{y}E"]["ufcf"] for y in years}})
    final = periods[f"FY{years[-1]}E"]
    cash_share = selections["terminal"]["terminal_cash_tax"]["value"] / 100
    g = growth + scenario.get("perpetuity_growth_delta", 0.0) / 100
    result = dcf(cash_flows=flows, schedule=schedule, horizon_t=horizon, wacc=rate,
                 terminal_ufcf=terminal_ufcf(final, cash_share), terminal_ebitda=final["ebitda"],
                 growth=g, exit_multiple=exit_multiple, mid_year=v.get("mid_year_convention", True))
    out = {"statements": statements, "periods": periods, "warnings": warnings, "wacc": wacc, "rate": rate,
           "schedule": schedule.assign(cash_flow=flows, discount_factor=result["discount_factors"],
                                       present_value=result["pv_by_period"]),
           "stub_fraction": stub_fraction, "dcf": result, "growth": g, "exit_multiple": exit_multiple}
    for method in ("perpetuity", "exit_multiple"):
        table, price = equity_bridge(result[method]["enterprise_value"], debt=inputs["debt"],
                                     cash=balance["cash"], adjustments=bridge_adjustments,
                                     diluted_shares=inputs["diluted_shares"])
        out[f"bridge_{method}"], out[f"price_{method}"] = table, price
    return out


def shifted_prices(values, years, wti=None, henry_hub=None):
    """Copy of values with a flat WTI and/or Henry Hub for every explicit year."""
    shifted = copy.deepcopy(values)
    for year in years:
        if wti is not None:
            shifted["wti"][year] = wti
        if henry_hub is not None:
            shifted["henry_hub"][year] = henry_hub
    return shifted


def sensitivities(values, selections, *, years, base_kwargs, base_rate, growth, exit_multiple):
    wacc_steps = [base_rate + d for d in (-0.02, -0.01, 0.0, 0.01, 0.02)]
    growth_steps = [growth + d for d in (-0.01, -0.005, 0.0, 0.005, 0.01)]
    multiple_steps = [exit_multiple + d for d in (-1.5, -0.75, 0.0, 0.75, 1.5)]
    wti_steps = [50.0, 60.0, 70.0, 80.0, 90.0]

    def price(vals, rate, g, m, method):
        return run_valuation(vals, selections, years=years, growth=g, exit_multiple=m,
                             wacc_override=rate, **base_kwargs)[f"price_{method}"]

    grids = {
        "WACC x terminal growth (perpetuity, USD/share)": sensitivity(
            lambda r, g: price(values, r, g, exit_multiple, "perpetuity"), wacc_steps, growth_steps, "WACC", "Terminal growth"),
        "WACC x exit multiple (USD/share)": sensitivity(
            lambda r, m: price(values, r, growth, m, "exit_multiple"), wacc_steps, multiple_steps, "WACC", "Exit EV/EBITDA"),
        "Flat WTI for FY2027E onward x WACC (perpetuity, USD/share)": sensitivity(
            lambda p, r: price(shifted_prices(values, years, wti=p), r, growth, exit_multiple, "perpetuity"),
            wti_steps, wacc_steps, "WTI (USD/Bbl)", "WACC"),
    }
    return grids
