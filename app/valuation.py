"""WACC, DCF, terminal values, enterprise-to-equity bridge, scenarios and sensitivities.

Pure calculations: all inputs are passed in; nothing is retrieved here.
Rates are decimals inside this module (0.08 = 8%). Amounts are USD millions,
shares are millions, per-share values are USD.
"""

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd


class ValuationError(ValueError):
    pass


def _check(value, name, *, low=None, high=None):
    if value is None or isinstance(value, bool) or not math.isfinite(value):
        raise ValuationError(f"{name} is missing or not finite.")
    if low is not None and value < low:
        raise ValuationError(f"{name} is below {low}.")
    if high is not None and value > high:
        raise ValuationError(f"{name} is above {high}.")
    return float(value)


def relever_beta(unlevered, debt, equity, tax_rate):
    """Hamada: beta_L = beta_U x (1 + (1 - t) x D/E)."""
    _check(unlevered, "Unlevered beta", low=0)
    _check(debt, "Debt", low=0)
    _check(equity, "Market equity", low=1e-9)
    return unlevered * (1 + (1 - tax_rate) * debt / equity)


@dataclass
class WACC:
    risk_free: float
    beta: float
    equity_risk_premium: float
    pre_tax_cost_of_debt: float
    tax_rate: float
    equity_value: float
    debt_value: float

    @property
    def cost_of_equity(self):
        return self.risk_free + self.beta * self.equity_risk_premium

    @property
    def after_tax_cost_of_debt(self):
        return self.pre_tax_cost_of_debt * (1 - self.tax_rate)

    @property
    def equity_weight(self):
        return self.equity_value / (self.equity_value + self.debt_value)

    @property
    def debt_weight(self):
        return 1 - self.equity_weight

    @property
    def wacc(self):
        return self.equity_weight * self.cost_of_equity + self.debt_weight * self.after_tax_cost_of_debt

    def table(self):
        return pd.DataFrame({"Value": {
            "Risk-free rate": self.risk_free, "Beta (levered)": self.beta,
            "Equity risk premium": self.equity_risk_premium, "Cost of equity": self.cost_of_equity,
            "Pre-tax cost of debt": self.pre_tax_cost_of_debt, "Tax rate": self.tax_rate,
            "After-tax cost of debt": self.after_tax_cost_of_debt,
            "Market equity (USD millions)": self.equity_value, "Debt (USD millions)": self.debt_value,
            "Equity weight": self.equity_weight, "Debt weight": self.debt_weight, "WACC": self.wacc}})


def build_wacc(*, risk_free, unlevered_beta, equity_risk_premium, pre_tax_cost_of_debt, tax_rate,
               share_price, diluted_shares, debt):
    for value, name in [(risk_free, "Risk-free rate"), (equity_risk_premium, "ERP"),
                        (pre_tax_cost_of_debt, "Pre-tax cost of debt")]:
        _check(value, name, low=0, high=0.5)
    _check(tax_rate, "Tax rate", low=0, high=1)
    equity = _check(share_price, "Share price", low=1e-9) * _check(diluted_shares, "Diluted shares", low=1e-9)
    beta = relever_beta(unlevered_beta, debt, equity, tax_rate)
    return WACC(risk_free, beta, equity_risk_premium, pre_tax_cost_of_debt, tax_rate, equity, debt)


def discount_schedule(valuation_date, stub_end, years, *, mid_year=True):
    """Discount timing in years from the valuation date.

    The stub (valuation date to ``stub_end``) is followed by full fiscal years.
    With the mid-year convention each cash flow arrives in the middle of its period.
    """
    if stub_end <= valuation_date:
        raise ValuationError("The stub period must end after the valuation date.")
    stub_years = (stub_end - valuation_date).days / 365.25
    rows = [{"period": "stub", "years_in_period": stub_years,
             "t": stub_years / 2 if mid_year else stub_years}]
    for i, year in enumerate(years, start=1):
        end = stub_years + i
        rows.append({"period": year, "years_in_period": 1.0, "t": end - 0.5 if mid_year else end})
    return pd.DataFrame(rows).set_index("period"), stub_years + len(years)


def dcf(*, cash_flows, schedule, horizon_t, wacc, terminal_ufcf, terminal_ebitda, growth, exit_multiple,
        mid_year=True):
    """Return dict with PVs and both terminal-value methods.

    cash_flows: Series indexed like ``schedule`` (stub cash flow already prorated).
    terminal_ufcf: normalized final-year UFCF (fully cash-taxed).
    Perpetuity TV at the horizon = UFCF x (1+g)/(WACC-g); with mid-year flows it is
    multiplied by (1+WACC)^0.5 so the first terminal flow also arrives mid-year.
    Exit-multiple TV = final-year EBITDA x multiple, valued at the horizon.
    """
    _check(wacc, "WACC", low=1e-6, high=0.5)
    _check(growth, "Terminal growth", low=-0.05, high=0.1)
    if wacc <= growth:
        raise ValuationError("WACC must exceed the terminal growth rate.")
    _check(exit_multiple, "Exit multiple", low=0, high=50)
    factors = 1 / (1 + wacc) ** schedule["t"]
    pv = cash_flows.reindex(schedule.index) * factors
    if pv.isna().any():
        raise ValuationError("A cash flow is missing for a discount period.")
    horizon_factor = 1 / (1 + wacc) ** horizon_t
    tv_perpetuity = terminal_ufcf * (1 + growth) / (wacc - growth) * ((1 + wacc) ** 0.5 if mid_year else 1)
    tv_exit = terminal_ebitda * exit_multiple
    out = {"pv_explicit": float(pv.sum()), "discount_factors": factors, "pv_by_period": pv}
    for name, tv in [("perpetuity", tv_perpetuity), ("exit_multiple", tv_exit)]:
        out[name] = {"terminal_value": tv, "pv_terminal": tv * horizon_factor,
                     "enterprise_value": float(pv.sum()) + tv * horizon_factor}
    # Cross-checks: what each method implies about the other.
    out["perpetuity"]["implied_exit_multiple"] = tv_perpetuity / terminal_ebitda if terminal_ebitda else float("nan")
    tv_exit_end = tv_exit / ((1 + wacc) ** 0.5 if mid_year else 1)
    out["exit_multiple"]["implied_growth"] = (tv_exit_end * wacc - terminal_ufcf) / (tv_exit_end + terminal_ufcf)
    return out


def equity_bridge(enterprise_value, *, debt, cash, adjustments, diluted_shares):
    """EV - debt + cash +/- listed adjustments = equity; / diluted shares = per share.

    ``adjustments``: list of (label, signed amount) already in equity-value direction.
    """
    _check(diluted_shares, "Diluted shares", low=1e-9)
    rows = [("Enterprise value", enterprise_value), ("Less: debt", -debt), ("Plus: cash", cash)]
    rows += list(adjustments)
    equity = sum(amount for _, amount in rows)
    rows += [("Equity value", equity), ("Diluted shares (millions)", diluted_shares),
             ("Implied value per share (USD)", equity / diluted_shares)]
    return pd.DataFrame(rows, columns=["Item", "Value"]).set_index("Item"), equity / diluted_shares


def terminal_ufcf(final_period, terminal_cash_tax_share=1.0):
    """Remove the deferred-tax benefit for the perpetuity (fully cash-taxed)."""
    return final_period["ufcf"] - final_period["deferred_tax"] * terminal_cash_tax_share


def sensitivity(func, rows, cols, row_name, col_name):
    """Grid of func(row_value, col_value); errors (e.g. WACC <= g) become NaN."""
    data = {}
    for c in cols:
        column = {}
        for r in rows:
            try:
                column[r] = func(r, c)
            except ValuationError:
                column[r] = float("nan")
        data[c] = column
    frame = pd.DataFrame(data)
    frame.index.name, frame.columns.name = row_name, col_name
    return frame
