"""Small accounting examples; no live services or company data required."""

from types import SimpleNamespace

import pandas as pd
import pytest

from app.history_review import accounting_checks, operating_driver_review


def history(**changes):
    values = dict(assets=100, liabilities=60, equity=40,
                  pretax_income=20, income_tax=5, net_income=15,
                  revenue=70, operating_expenses_before_da=40, dd_a=10, ebit=20)
    values.update(changes)
    return SimpleNamespace(provider="WRDS", annual=pd.DataFrame([values], index=[2030]))


def test_three_independent_accounting_bridges_tie():
    result = accounting_checks(history())
    assert list(result.Status) == ["Ties"] * 3
    assert list(result["Residual (USD millions)"]) == [0] * 3


def test_missing_tax_is_not_zero_and_does_not_pass():
    result = accounting_checks(history(income_tax=float("nan"))).set_index("Check")
    assert result.loc["Net income", "Status"] == "Missing input"
    assert result.loc["Net income", "Missing fields"] == "income_tax"
    assert pd.isna(result.loc["Net income", "Residual (USD millions)"])


def test_difference_is_visible_without_changing_inputs():
    source = history(equity=39)
    result = accounting_checks(source).set_index("Check")
    assert result.loc["Balance sheet", "Status"] == "Review difference"
    assert result.loc["Balance sheet", "Residual (USD millions)"] == 1
    assert source.annual.loc[2030, "equity"] == 39


def test_compustat_expense_check_is_not_applied_to_sec():
    source = history()
    source.provider = "SEC"
    result = accounting_checks(source).set_index("Check")
    assert "Compustat operating income" not in result.index
    assert result.loc["Filed operating income", "Status"] == "Missing input"


def test_sec_operating_income_uses_total_reported_expenses():
    source = history(operating_expenses=50)
    source.provider = "SEC"
    result = accounting_checks(source).set_index("Check")
    assert result.loc["Filed operating income", "Status"] == "Ties"


@pytest.mark.parametrize("tolerance", [-1, float("nan"), float("inf"), True])
def test_invalid_tolerance_rejected(tolerance):
    with pytest.raises(ValueError):
        accounting_checks(history(), tolerance=tolerance)


def test_operating_review_matches_year_labels_and_distinguishes_percentage_points():
    annual = pd.DataFrame({"revenue": [100, 100], "ebit": [10, 20],
                           "capex": [50, 60]}, index=[2031, 2030])
    operating = pd.DataFrame({"annual_mmboe": [10, 20], "loe_boe": [1, 2],
                             "gpt_boe": [2, 3], "ga_boe": [3, 4]}, index=[2030, 2031])
    result = operating_driver_review(annual, operating)
    assert result.loc["Annual production", "Change (%)"] == 100
    assert result.loc["Capex per Boe", "2031"] == 2.5
    assert result.loc["Operating margin", "Change"] == -10
    assert result.loc["Operating margin", "Change unit"] == "percentage points"
    assert pd.isna(result.loc["Operating margin", "Change (%)"])
    assert result.loc["Selected unit-cost subtotal", "2031"] == 9


def test_operating_review_missing_year_and_component_remain_missing():
    annual = pd.DataFrame({"capex": [100, 200]}, index=[2030, 2032])
    operating = pd.DataFrame({"annual_mmboe": [10, 20], "loe_boe": [1, 2],
                             "gpt_boe": [2, 3]}, index=[2030, 2032])
    result = operating_driver_review(annual, operating)
    assert pd.isna(result.loc["Annual production", "2031"])
    assert pd.isna(result.loc["Annual production", "Change (%)"])
    assert pd.isna(result.loc["Selected unit-cost subtotal", "2032"])


def test_operating_review_does_not_carry_forward_missing_new_year():
    annual = pd.DataFrame({"capex": [100, 200]}, index=[2030, 2031])
    operating = pd.DataFrame({"annual_mmboe": [10]}, index=[2030])
    result = operating_driver_review(annual, operating)
    assert pd.isna(result.loc["Annual production", "2031"])
    assert pd.isna(result.loc["Capex per Boe", "2031"])
