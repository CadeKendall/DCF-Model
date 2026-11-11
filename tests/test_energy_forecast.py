"""Forecast arithmetic on small synthetic inputs."""

import pandas as pd
import pytest

from app.energy_forecast import _period, build_forecast, fy_base_bridge

YEARS = [2027, 2028]


def values(**overrides):
    flat = {
        "oil_growth": 10.0, "ngl_growth": 0.0, "us_gas_growth": 0.0, "intl_gas_growth": 0.0,
        "wti": 70.0, "henry_hub": 3.0, "oil_differential": 2.0, "ngl_pct_wti": 30.0,
        "us_gas_differential": 0.5, "intl_gas_price": 4.0, "cost_inflation": 0.0,
        "lease_well_boe": 4.0, "gpt_boe": 5.0, "ga_boe": 1.5, "dd_a_boe": 10.0, "toti_pct": 7.0,
        "exploration_dry_hole": 100.0, "impairments": 50.0, "marketing_margin": 20.0, "other_revenue": 10.0,
        "operating_tax_rate": 20.0, "current_tax_share": 80.0, "capex_per_boe": 12.0,
        "capitalized_interest": 10.0, "aro_settlements": 5.0, "nwc_pct_sales": 5.0,
    }
    flat.update(overrides)
    out = {k: {y: v for y in YEARS} for k, v in flat.items()}
    stub = dict(flat, oil_growth=100.0, ngl_growth=50.0, us_gas_growth=600.0, intl_gas_growth=60.0,
                capex_per_boe=300.0, exploration_dry_hole=25.0, impairments=12.0,
                marketing_margin=5.0, other_revenue=2.5, aro_settlements=1.0)
    for k, v in stub.items():
        if k != "cost_inflation":
            out[k]["stub"] = v
    return out


NINE_MONTH = pd.Series({"exploration": 75.0, "dry_hole": 25.0, "oil_mbbl_day": 100.0, "ngl_mbbl_day": 50.0,
                        "gas_mmcf_day": 660.0, "total_mboe_day": 260.0, "capex_nongaap": 900.0})
BALANCE = pd.Series({"receivables": 300.0, "inventory": 50.0, "accounts_payable": 250.0})


def test_one_commodity_revenue_hand_example():
    r = _period(days=365, volumes={"oil": 100, "ngl": 0, "us_gas": 0, "intl_gas": 0},
                prices={"wti": 70, "henry_hub": 3, "oil_differential": 2, "ngl_pct_wti": 30,
                        "us_gas_differential": 0, "intl_gas_price": 0},
                rates={"lease_well_boe": 0, "gpt_boe": 0, "ga_boe": 0, "dd_a_boe": 10, "toti_pct": 0},
                fixed={"marketing_margin": 0, "other_revenue": 0, "exploration": 0, "dry_hole": 0,
                       "impairments": 0, "capex": 300, "capitalized_interest": 0, "aro": 0},
                tax_rate=20, current_share=100, nwc_begin=0, nwc_pct=0, annualize=1)
    # 100 MBbl/d x 365 / 1000 = 36.5 MMBbl x $72 = $2,628 million.
    assert r["oil_sales"] == pytest.approx(2628)
    assert r["dd_a"] == pytest.approx(365)
    assert r["ebit"] == pytest.approx(2628 - 365)
    assert r["ufcf"] == pytest.approx((2628 - 365) * 0.8 + 365 - 300)


def test_gas_priced_separately_and_boe_only_for_costs():
    r = _period(days=366, volumes={"oil": 0, "ngl": 0, "us_gas": 600, "intl_gas": 0},
                prices={"wti": 70, "henry_hub": 3, "oil_differential": 0, "ngl_pct_wti": 30,
                        "us_gas_differential": 0.5, "intl_gas_price": 0},
                rates={"lease_well_boe": 1, "gpt_boe": 0, "ga_boe": 0, "dd_a_boe": 0, "toti_pct": 0},
                fixed={"marketing_margin": 0, "other_revenue": 0, "exploration": 0, "dry_hole": 0,
                       "impairments": 0, "capex": 0, "capitalized_interest": 0, "aro": 0},
                tax_rate=0, current_share=100, nwc_begin=0, nwc_pct=0, annualize=1)
    assert r["us_gas_sales"] == pytest.approx(600 * 366 / 1000 * 3.5)  # leap year
    assert r["lease_well"] == pytest.approx(600 / 6 * 366 / 1000)


def test_forecast_growth_capex_and_ufcf_identity():
    statements, periods, warnings = build_forecast(values(), base_year=2026, stub_quarter=4, years=YEARS,
                                                   nine_month=NINE_MONTH, balance=BALANCE)
    assert periods["FY2027E"]["oil_volume"] == pytest.approx(110 * 365 / 1000)
    assert periods["FY2028E"]["oil_volume"] == pytest.approx(121 * 366 / 1000)
    for p in periods.values():
        identity = (p["nopat"] + p["dd_a"] + p["impairments"] + p["deferred_tax"] - p["capex_ufcf"]
                    - p["change_nwc"] - p["aro"])
        assert p["ufcf"] == pytest.approx(identity)
        assert p["capex_ufcf"] == pytest.approx(p["capex"] - p["capitalized_interest"] - p["dry_hole"])
    # Stub NWC starts from the reported balance: 300 + 50 - 250 = 100.
    assert periods["2026Q4E"]["change_nwc"] == pytest.approx(periods["2026Q4E"]["nwc_end"] - 100)
    # Dry-hole share comes from the nine-month mix (25%).
    assert periods["FY2027E"]["dry_hole"] == pytest.approx(25.0)
    assert "Unlevered free cash flow" in statements.index


def test_scenario_shifts_prices_only_in_explicit_years():
    _, base, _ = build_forecast(values(), base_year=2026, stub_quarter=4, years=YEARS,
                                nine_month=NINE_MONTH, balance=BALANCE)
    _, bear, _ = build_forecast(values(), base_year=2026, stub_quarter=4, years=YEARS,
                                nine_month=NINE_MONTH, balance=BALANCE, scenario={"wti_delta": -10})
    assert bear["2026Q4E"]["wti"] == base["2026Q4E"]["wti"]
    assert bear["FY2027E"]["wti"] == base["FY2027E"]["wti"] - 10


def test_base_year_bridge_checks_guidance_range():
    _, periods, _ = build_forecast(values(), base_year=2026, stub_quarter=4, years=YEARS,
                                   nine_month=NINE_MONTH, balance=BALANCE)
    table = fy_base_bridge(NINE_MONTH, periods["2026Q4E"], base_year=2026,
                           guidance_ranges={"oil_mbbl_day": (99.0, 101.0), "capex_nongaap": (1100, 1300)})
    # 9M at 100 and Q4 at 100 -> FY 100: inside. Capex 900 + 300 = 1,200: inside.
    assert table.loc["Oil (MBbl/day)", "FY2026E"] == pytest.approx(100)
    assert table.loc["Oil (MBbl/day)", "Status"] == "Inside FY guidance"
    assert table.loc["Capex, guidance basis (USD millions)", "FY2026E"] == 1200
    assert table.loc["NGL (MBbl/day)", "Status"] == "No range"


def test_negative_volume_rejected():
    with pytest.raises(ValueError):
        build_forecast(values(oil_growth=-150.0), base_year=2026, stub_quarter=4, years=YEARS,
                       nine_month=NINE_MONTH, balance=BALANCE)
