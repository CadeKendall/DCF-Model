"""Forward assumptions table: resolve editable selections against sourced inputs.

Layers stay separate:
- history_references(): calculated from the notebook's historical outputs (Sections 9-11).
- guidance frame: app.guidance (verified against SEC documents).
- STEO prices: app.eia.
- selections: data/assumptions/<TICKER>_assumptions.json (analyst choices or rules).
resolve_assumptions() returns one row per driver and period with its source,
selected value, rationale and status; nothing is silently carried forward.
"""

import json
import math
from datetime import date
from pathlib import Path

import pandas as pd

from app.guidance import guidance_value

STATUSES_BLOCKING = {"Decision needed"}


class AssumptionError(ValueError):
    pass


def days_in_year(year):
    return (date(int(year) + 1, 1, 1) - date(int(year), 1, 1)).days


def history_references(*, annual, revenue_disclosures, revenue_bridge, cost_bridge, cost_unit_metrics,
                       cashflow_review, aro_settled, steo_annual):
    """Year-indexed historical drivers in forecast units. Missing inputs stay NaN."""
    years = annual.index
    refs = pd.DataFrame(index=years)
    rd = revenue_disclosures.reindex(years)
    for col in ("oil_mbbl_day", "ngl_mbbl_day", "gas_mmcf_day", "realized_oil", "realized_ngl", "realized_gas"):
        refs[col] = rd.get(col)
    bridge = revenue_bridge.reindex(years)
    refs["upstream_sales"] = bridge["Reported upstream sales"]
    refs["marketing_margin"] = bridge["Marketing revenue less marketing costs"]
    refs["other_revenue"] = bridge["Other revenue, net"]
    units = cost_unit_metrics.reindex(years)
    refs["lease_well_boe"] = units["Lease and well / Boe (USD)"]
    refs["gpt_boe"] = units["Gathering, processing and transportation / Boe (USD)"]
    refs["ga_boe"] = units["General and administrative / Boe (USD)"]
    refs["dd_a_boe"] = units["Depreciation, depletion and amortization / Boe (USD)"]
    refs["toti_pct"] = units["Taxes other than income / upstream sales (%)"]
    costs = cost_bridge.reindex(years)
    refs["exploration_dry_hole"] = costs["Exploration"] + costs["Dry-hole costs"]
    refs["impairments"] = costs["Impairments"]
    taxes = cashflow_review["taxes"].reindex(years)
    refs["effective_tax_rate"] = taxes["Effective book tax rate (%)"]
    provision = taxes["Reported income tax expense"].where(taxes["Reported income tax expense"] > 0)
    refs["current_tax_share"] = 100 * taxes["Current tax provision"] / provision
    investing = cashflow_review["investing"].reindex(years)
    refs["property_additions"] = investing["Property additions before investing WC"]
    refs["capitalized_interest"] = investing["Capitalized interest (memo; do not add to spending again)"]
    mmboe = units["Calculated production (MMBoe)"]
    refs["property_additions_per_boe"] = refs["property_additions"] / mmboe.where(mmboe > 0)
    nwc = annual["receivables"] + annual["inventory"] - annual["accounts_payable"]
    refs["nwc_pct_sales"] = 100 * nwc / refs["upstream_sales"].where(refs["upstream_sales"] > 0)
    refs["aro_settled"] = pd.Series(aro_settled).reindex(years)
    prices = steo_annual.reindex(years)
    refs["wti"] = prices["wti"]
    refs["henry_hub"] = prices["henry_hub"]
    refs["oil_differential"] = refs["realized_oil"] - refs["wti"]
    refs["ngl_pct_wti"] = 100 * refs["realized_ngl"] / refs["wti"]
    refs["gas_vs_henry_hub"] = refs["realized_gas"] - refs["henry_hub"]
    return refs


def load_selections(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("valuation", "drivers", "wacc", "terminal", "scenarios"):
        if key not in payload:
            raise AssumptionError(f"Assumptions file lacks '{key}'.")
    names = [d["driver"] for d in payload["drivers"]]
    if len(names) != len(set(names)):
        raise AssumptionError("Duplicate driver names in assumptions file.")
    return payload


def forecast_years(selections):
    v = selections["valuation"]
    base, horizon = int(v["base_year"]), int(v["explicit_years"])
    if horizon < 1:
        raise AssumptionError("explicit_years must be at least 1.")
    return list(range(base + 1, base + 1 + horizon))


def _finite(value, label):
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AssumptionError(f"{label}: resolved value is missing or not finite.")
    return float(value)


def _resolve(spec, year, ctx, driver):
    """Return (value, source text). ``year`` is an int or 'stub'."""
    if isinstance(spec, (int, float)) and not isinstance(spec, bool):
        return float(spec), "Explicit value in assumptions file"
    if not isinstance(spec, dict) or "rule" not in spec:
        raise AssumptionError(f"{driver}: unsupported specification {spec!r}.")
    rule = spec["rule"]
    guidance = ctx["guidance"]
    base_year = ctx["base_year"]
    if rule in {"guidance", "guidance_amount"}:
        period = f"{base_year}Q{ctx['stub_quarter']}" if spec["period"] == "Q4" else f"{base_year}FY"
        value = guidance_value(guidance, spec["metric"], period)
        if value is None:
            raise AssumptionError(f"{driver}: no {spec['metric']} guidance for {period}.")
        return value, f"EOG guidance midpoint {period} (8-K filed {ctx['guidance_filed']})"
    if rule == "guidance_difference":
        period = f"{base_year}Q{ctx['stub_quarter']}" if spec["period"] == "Q4" else f"{base_year}FY"
        a, b = guidance_value(guidance, spec["metric"], period), guidance_value(guidance, spec["less"], period)
        if a is None or b is None:
            raise AssumptionError(f"{driver}: guidance difference unavailable for {period}.")
        return a - b, f"EOG guidance {spec['metric']} less {spec['less']}, {period}"
    if rule == "guidance_per_boe":
        period = f"{base_year}FY"
        amount = guidance_value(guidance, spec["metric"], period)
        boe = guidance_value(guidance, "total_mboe_day", period)
        if amount is None or not boe:
            raise AssumptionError(f"{driver}: guidance per Boe unavailable.")
        return amount / (boe * days_in_year(base_year) / 1000), f"EOG {period} guided {spec['metric']} / guided MMBoe"
    if rule == "steo_annual":
        steo = ctx["steo_annual"]
        forecast = steo[steo.forecast_months > 0]
        last = int(forecast.index.max())
        if year in steo.index:
            return float(steo.at[year, spec["series"]]), f"EIA STEO {ctx['steo_release']} ({steo.at[year, 'status']})"
        after = spec.get("after_last")
        if year > last and after == "hold_last":
            return float(steo.at[last, spec["series"]]), f"EIA STEO {ctx['steo_release']} {last} average held flat"
        if year > last and isinstance(after, (int, float)) and not isinstance(after, bool):
            return float(after), f"Long-term flat deck (analyst value) after STEO horizon {last}"
        raise AssumptionError(f"{driver}: no STEO value for {year}.")
    if rule == "steo_quarter":
        q = ctx["steo_stub"]
        return float(q[spec["series"]]), f"EIA STEO {ctx['steo_release']} {base_year}Q{ctx['stub_quarter']} ({q['status']})"
    if rule == "historical_median":
        series = ctx["refs"][spec["metric"]].dropna()
        if len(series) < 3:
            raise AssumptionError(f"{driver}: fewer than 3 historical observations for {spec['metric']}.")
        scale = float(spec.get("scale", 1.0))
        years = f"FY{series.index.min()}-FY{series.index.max()}"
        note = f"Median {years}" + (f" x {scale:g} (quarter)" if scale != 1 else "")
        return float(series.median()) * scale, note
    raise AssumptionError(f"{driver}: unknown rule {rule!r}.")


def _history_text(refs, metric):
    if refs is None or metric not in refs:
        return ""
    series = refs[metric].dropna()
    if series.empty:
        return "unavailable"
    return f"FY{series.index.max()} {series.iloc[-1]:,.2f}; median {series.median():,.2f} (FY{series.index.min()}-{series.index.max()})"


REFERENCE_METRIC = {
    "oil_growth": "oil_mbbl_day", "ngl_growth": "ngl_mbbl_day", "us_gas_growth": "gas_mmcf_day",
    "intl_gas_growth": "gas_mmcf_day", "wti": "wti", "henry_hub": "henry_hub",
    "oil_differential": "oil_differential", "ngl_pct_wti": "ngl_pct_wti", "us_gas_differential": "gas_vs_henry_hub",
    "intl_gas_price": "realized_gas", "lease_well_boe": "lease_well_boe", "gpt_boe": "gpt_boe", "ga_boe": "ga_boe",
    "dd_a_boe": "dd_a_boe", "toti_pct": "toti_pct", "exploration_dry_hole": "exploration_dry_hole",
    "impairments": "impairments", "marketing_margin": "marketing_margin", "other_revenue": "other_revenue",
    "operating_tax_rate": "effective_tax_rate", "current_tax_share": "current_tax_share",
    "capex_per_boe": "property_additions_per_boe", "capitalized_interest": "capitalized_interest",
    "aro_settlements": "aro_settled", "nwc_pct_sales": "nwc_pct_sales",
}

GUIDANCE_METRIC = {
    "oil_growth": "oil_mbbl_day", "ngl_growth": "ngl_mbbl_day", "us_gas_growth": "us_gas_mmcf_day",
    "oil_differential": "oil_differential_us", "ngl_pct_wti": "ngl_pct_wti", "us_gas_differential": "gas_differential_us",
    "intl_gas_price": "realized_trinidad_gas", "lease_well_boe": "lease_well_boe", "gpt_boe": "gpt_boe",
    "ga_boe": "ga_boe", "dd_a_boe": "dd_a_boe", "toti_pct": "toti_pct", "exploration_dry_hole": "exploration_dry_hole",
    "impairments": "impairments", "operating_tax_rate": "effective_tax_rate", "capex_per_boe": "capex_nongaap",
    "capitalized_interest": "capitalized_interest",
}


def _guidance_text(guidance, metric, base_year, stub_quarter):
    if metric is None:
        return "None in SEC filings"
    parts = []
    for period in (f"{base_year}Q{stub_quarter}", f"{base_year}FY"):
        rows = guidance[(guidance.metric == metric) & (guidance.period == period) & guidance.low.notna()]
        if not rows.empty:
            r = rows.sort_values("kind").iloc[0]
            parts.append(f"{period}: {r.low:g} to {r.high:g} (mid {r.point:g}) {r.units}")
    return "; ".join(parts) if parts else "None in SEC filings"


def resolve_assumptions(selections, *, refs, guidance, steo_annual, steo_stub, steo_release):
    """Return (values: dict driver -> {period: value}, table DataFrame)."""
    v = selections["valuation"]
    base_year, stub_quarter = int(v["base_year"]), int(v["stub_quarter"])
    years = forecast_years(selections)
    filed = guidance["filed"].max() if "filed" in guidance else "n/a"
    ctx = {"guidance": guidance, "base_year": base_year, "stub_quarter": stub_quarter, "refs": refs,
           "steo_annual": steo_annual, "steo_stub": steo_stub, "steo_release": steo_release,
           "guidance_filed": filed}
    values, rows = {}, []
    for item in selections["drivers"]:
        driver, status = item["driver"], item.get("status", "Decision needed")
        if status in STATUSES_BLOCKING:
            raise AssumptionError(f"{driver}: status is 'Decision needed'. Select a value before forecasting.")
        values[driver] = {}
        periods = (["stub"] if "stub" in item else []) + years
        for period in periods:
            if period == "stub":
                spec = item["stub"]
            else:
                spec = item["values"].get(str(period), item["values"].get("default"))
            if spec is None:
                raise AssumptionError(f"{driver}: no value for {period} and no default.")
            value, source = _resolve(spec, period, ctx, driver)
            values[driver][period] = _finite(value, f"{driver} {period}")
            rows.append({
                "driver": driver, "label": item["label"], "category": item["category"],
                "units": item.get("stub_units", item["units"]) if period == "stub" else item["units"],
                "forecast period": f"{base_year}Q{stub_quarter} (stub)" if period == "stub" else f"FY{period}E",
                "historical reference": _history_text(refs, REFERENCE_METRIC.get(driver)),
                "source / as-of": source,
                "management guidance": _guidance_text(guidance, GUIDANCE_METRIC.get(driver), base_year, stub_quarter),
                "selected assumption": values[driver][period],
                "rationale": item["rationale"] + (f" Base: {item['base']}." if item.get("base") else ""),
                "status": "Management guidance" if period == "stub" and isinstance(spec, dict) and spec.get("rule", "").startswith("guidance") else status,
            })
    table = pd.DataFrame(rows)
    return values, table


def validate_against_guidance(values, guidance, base_year, stub_quarter):
    """Stub selections must sit inside the printed Q4 guidance range."""
    checks = []
    pairs = {"oil_growth": "oil_mbbl_day", "ngl_growth": "ngl_mbbl_day", "us_gas_growth": "us_gas_mmcf_day",
             "lease_well_boe": "lease_well_boe", "gpt_boe": "gpt_boe", "ga_boe": "ga_boe", "dd_a_boe": "dd_a_boe"}
    period = f"{base_year}Q{stub_quarter}"
    for driver, metric in pairs.items():
        if "stub" not in values.get(driver, {}):
            continue
        row = guidance[(guidance.metric == metric) & (guidance.period == period) & guidance.low.notna()]
        if row.empty:
            continue
        r = row.iloc[0]
        selected = values[driver]["stub"]
        checks.append({"driver": driver, "period": period, "selected": selected, "low": r.low, "high": r.high,
                       "Status": "Inside guidance range" if r.low <= selected <= r.high else "Outside guidance range"})
    return pd.DataFrame(checks)
