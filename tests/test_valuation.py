"""Transparent valuation arithmetic; no network or company data."""

from datetime import date

import pandas as pd
import pytest

from app.valuation import (ValuationError, build_wacc, dcf, discount_schedule, equity_bridge,
                           relever_beta, sensitivity, terminal_ufcf)


def test_relevered_beta_hamada():
    # 0.6 x (1 + 0.8 x 25/100) = 0.72
    assert relever_beta(0.6, 25, 100, 0.2) == pytest.approx(0.72)


def test_wacc_components():
    w = build_wacc(risk_free=0.04, unlevered_beta=1.0, equity_risk_premium=0.05, pre_tax_cost_of_debt=0.06,
                   tax_rate=0.25, share_price=10, diluted_shares=75, debt=250)
    # Equity 750, debt 250. Beta = 1 x (1 + 0.75 x 250/750) = 1.25. Ke = 0.04 + 1.25 x 0.05 = 0.1025.
    assert w.beta == pytest.approx(1.25)
    assert w.cost_of_equity == pytest.approx(0.1025)
    assert w.after_tax_cost_of_debt == pytest.approx(0.045)
    assert w.wacc == pytest.approx(0.75 * 0.1025 + 0.25 * 0.045)


def test_missing_price_is_rejected():
    with pytest.raises(ValuationError):
        build_wacc(risk_free=0.04, unlevered_beta=1.0, equity_risk_premium=0.05, pre_tax_cost_of_debt=0.06,
                   tax_rate=0.25, share_price=float("nan"), diluted_shares=75, debt=250)


def test_discount_schedule_stub_and_mid_year():
    schedule, horizon = discount_schedule(date(2030, 7, 2), date(2031, 1, 1), [2031, 2032])
    stub = (date(2031, 1, 1) - date(2030, 7, 2)).days / 365.25
    assert schedule.loc["stub", "t"] == pytest.approx(stub / 2)
    assert schedule.loc[2031, "t"] == pytest.approx(stub + 0.5)
    assert horizon == pytest.approx(stub + 2)


def simple_dcf(**changes):
    schedule = pd.DataFrame({"t": [1.0, 2.0]}, index=["stub", 2031])
    kwargs = dict(cash_flows=pd.Series({"stub": 100.0, 2031: 100.0}), schedule=schedule, horizon_t=2.0,
                  wacc=0.10, terminal_ufcf=100.0, terminal_ebitda=200.0, growth=0.02, exit_multiple=5.0,
                  mid_year=False)
    kwargs.update(changes)
    return dcf(**kwargs)


def test_dcf_both_terminal_methods_end_of_period():
    result = simple_dcf()
    pv = 100 / 1.1 + 100 / 1.1 ** 2
    assert result["pv_explicit"] == pytest.approx(pv)
    tv = 100 * 1.02 / 0.08
    assert result["perpetuity"]["terminal_value"] == pytest.approx(tv)
    assert result["perpetuity"]["enterprise_value"] == pytest.approx(pv + tv / 1.21)
    assert result["exit_multiple"]["enterprise_value"] == pytest.approx(pv + 1000 / 1.21)
    assert result["perpetuity"]["implied_exit_multiple"] == pytest.approx(tv / 200)


def test_mid_year_perpetuity_adjustment():
    result = simple_dcf(mid_year=True)
    assert result["perpetuity"]["terminal_value"] == pytest.approx(100 * 1.02 / 0.08 * 1.1 ** 0.5)


def test_wacc_must_exceed_growth():
    with pytest.raises(ValuationError):
        simple_dcf(wacc=0.02, growth=0.02)


def test_equity_bridge_signs():
    table, price = equity_bridge(1000, debt=300, cash=50, adjustments=[("Less: dividends payable", -10)],
                                 diluted_shares=37)
    assert table.loc["Equity value", "Value"] == 740
    assert price == pytest.approx(20)


def test_terminal_ufcf_removes_deferred_tax():
    assert terminal_ufcf({"ufcf": 500, "deferred_tax": 40}) == 460


def test_sensitivity_marks_invalid_cells_nan():
    def f(r, g):
        if r <= g:
            raise ValuationError("invalid")
        return r - g
    grid = sensitivity(f, [0.05, 0.08], [0.06], "WACC", "g")
    assert pd.isna(grid.loc[0.05, 0.06]) and grid.loc[0.08, 0.06] == pytest.approx(0.02)
