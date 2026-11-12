"""Assumption resolution rules; synthetic guidance and prices."""

import copy

import pandas as pd
import pytest

from app.forecast_assumptions import AssumptionError, resolve_assumptions, validate_against_guidance

GUIDANCE = pd.DataFrame([
    {"metric": "oil_mbbl_day", "period": "2030Q4", "low": 90.0, "high": 110.0, "point": 100.0, "units": "MBbl/day", "kind": "image", "filed": "2030-11-01"},
    {"metric": "lease_well_boe", "period": "2030FY", "low": 3.5, "high": 4.5, "point": 4.0, "units": "USD/Boe", "kind": "image", "filed": "2030-11-01"},
    {"metric": "capex_nongaap", "period": "2030FY", "low": 900.0, "high": 1100.0, "point": 1000.0, "units": "USD millions", "kind": "image", "filed": "2030-11-01"},
    {"metric": "total_mboe_day", "period": "2030FY", "low": 90.0, "high": 110.0, "point": 100.0, "units": "MBoe/day", "kind": "image", "filed": "2030-11-01"},
])
STEO = pd.DataFrame({"wti": [80.0, 75.0], "henry_hub": [3.0, 3.5], "forecast_months": [2, 12],
                     "status": ["EIA mixed", "EIA forecast"]}, index=[2030, 2031])
REFS = pd.DataFrame({"marketing_margin": [10.0, 20.0, 30.0, 1000.0]}, index=[2026, 2027, 2028, 2029])

SELECTIONS = {
    "valuation": {"base_year": 2030, "stub_quarter": 4, "explicit_years": 3},
    "drivers": [
        {"driver": "oil_growth", "label": "Oil", "category": "Volumes", "units": "%", "stub": {"rule": "guidance", "metric": "oil_mbbl_day", "period": "Q4"},
         "values": {"default": 2.0, "2032": 1.0}, "rationale": "r", "status": "Analyst assumption"},
        {"driver": "wti", "label": "WTI", "category": "Prices", "units": "USD/Bbl",
         "values": {"default": {"rule": "steo_annual", "series": "wti", "after_last": 60.0}}, "rationale": "r", "status": "Benchmark forecast"},
        {"driver": "henry_hub", "label": "HH", "category": "Prices", "units": "USD/Mcf",
         "values": {"default": {"rule": "steo_annual", "series": "henry_hub", "after_last": "hold_last"}}, "rationale": "r", "status": "Benchmark forecast"},
        {"driver": "lease_well_boe", "label": "LOE", "category": "Costs", "units": "USD/Boe",
         "values": {"default": {"rule": "guidance", "metric": "lease_well_boe", "period": "FY"}}, "rationale": "r", "status": "Guidance"},
        {"driver": "capex_per_boe", "label": "Capex", "category": "Reinvestment", "units": "USD/Boe",
         "values": {"default": {"rule": "guidance_per_boe", "metric": "capex_nongaap", "period": "FY"}}, "rationale": "r", "status": "Guidance"},
        {"driver": "marketing_margin", "label": "Marketing", "category": "Revenue", "units": "USD millions",
         "values": {"default": {"rule": "historical_median", "metric": "marketing_margin"}}, "rationale": "r", "status": "Historical"},
    ],
}


def resolve(selections=SELECTIONS):
    return resolve_assumptions(selections, refs=REFS, guidance=GUIDANCE, steo_annual=STEO,
                               steo_stub={"wti": 85.0, "henry_hub": 3.2, "status": "EIA forecast"}, steo_release="Test")


def test_rules_resolve_with_sources():
    values, table = resolve()
    assert values["oil_growth"] == {"stub": 100.0, 2031: 2.0, 2032: 1.0, 2033: 2.0}
    assert values["wti"] == {2031: 75.0, 2032: 60.0, 2033: 60.0}
    assert values["henry_hub"][2033] == 3.5
    assert values["lease_well_boe"][2031] == 4.0
    assert values["capex_per_boe"][2031] == pytest.approx(1000 / (100 * 365 / 1000))
    assert values["marketing_margin"][2031] == 25.0  # median, robust to the 1000 outlier
    assert {"driver", "units", "forecast period", "historical reference", "source / as-of",
            "management guidance", "selected assumption", "rationale", "status"} <= set(table.columns)


def test_decision_needed_blocks_forecast():
    selections = copy.deepcopy(SELECTIONS)
    selections["drivers"][0]["status"] = "Decision needed"
    with pytest.raises(AssumptionError, match="Decision needed"):
        resolve(selections)


def test_missing_guidance_is_not_substituted():
    selections = copy.deepcopy(SELECTIONS)
    selections["drivers"][3]["values"]["default"]["metric"] = "ga_boe"
    with pytest.raises(AssumptionError, match="no ga_boe guidance"):
        resolve(selections)


def test_too_little_history_for_median():
    selections = copy.deepcopy(SELECTIONS)
    selections["drivers"][5]["values"]["default"]["metric"] = "absent"
    with pytest.raises(KeyError):
        resolve(selections)


def test_stub_inside_guidance_range():
    values, _ = resolve()
    checks = validate_against_guidance(values, GUIDANCE, 2030, 4)
    assert list(checks.Status) == ["Inside guidance range"]
