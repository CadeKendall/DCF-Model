"""Notebook WACC methodology, independent of data retrieval and the browser."""
from dataclasses import dataclass
from datetime import date
import math

import numpy as np
import pandas as pd


class CapitalError(ValueError):
    """An actionable, safe error for display in the browser."""


@dataclass(frozen=True)
class Financials:
    fiscal_year: int
    fiscal_end: str
    debt: float
    previous_debt: float | None
    interest: float
    taxes: float | None
    pretax_income: float | None
    equity: float
    company: str
    gvkey: str


def finite(value):
    return value is not None and math.isfinite(float(value))


def clean_returns(frame, name):
    if frame.empty:
        raise CapitalError(f"No CRSP returns found for {name} in this window.")
    frame = frame.copy()
    frame['date'] = pd.to_datetime(frame['date'])
    if frame['date'].duplicated().any():
        raise CapitalError(f"Multiple returns on one date for {name}; security identity needs review.")
    values = pd.to_numeric(frame['ret'], errors='coerce')
    valid = values.notna() & np.isfinite(values)
    if (values[valid] < -1).any():
        raise CapitalError(f"Invalid daily returns below -100% for {name}.")
    return pd.Series(values[valid].to_numpy(dtype=float), index=frame.loc[valid, 'date'], name=name).sort_index()


def calculate(stock_frame, market_frame, financials, risk_free, risk_free_date,
              ticker, proxy, start, end, corporate_tax=False):
    stock = clean_returns(stock_frame, ticker)
    market = clean_returns(market_frame, proxy)
    aligned = pd.concat([stock.rename('stock'), market.rename('market')], axis=1).dropna()
    if len(aligned) < 30:
        raise CapitalError('Choose a longer window: at least 30 overlapping daily returns are required.')
    variance = aligned['market'].var()
    if variance <= 0 or not finite(variance):
        raise CapitalError('Market returns have no usable variance; beta cannot be estimated.')
    if not finite(risk_free):
        raise CapitalError('The risk-free rate is unavailable.')
    beta = float(aligned['stock'].cov(aligned['market']) / variance)
    market_return = float(np.expm1(np.log1p(market).sum() * 252 / len(market))) if (market > -1).all() else -1.0
    f = financials
    if any(not finite(x) or x < 0 for x in [f.debt, f.interest, f.equity]) or f.equity <= 0:
        raise CapitalError('Debt, interest, and positive market equity are required; missing values are not zero.')
    warnings = []
    if f.previous_debt is None:
        average_debt = f.debt
        warnings.append('Previous-year debt is unavailable. Cost of debt uses current year-end debt, as in the notebook fallback.')
    else:
        if not finite(f.previous_debt) or f.previous_debt < 0:
            raise CapitalError('Previous-year debt is invalid.')
        average_debt = (f.debt + f.previous_debt) / 2
    if average_debt <= 0:
        raise CapitalError('Average debt is zero; the notebook cost-of-debt formula cannot be calculated.')
    effective_tax = (f.taxes / f.pretax_income
                     if finite(f.taxes) and finite(f.pretax_income) and f.pretax_income != 0 else None)
    if not corporate_tax and effective_tax is None:
        raise CapitalError('Effective tax rate is unavailable or pretax income is zero. Select the 21% override to continue.')
    tax = 0.21 if corporate_tax else effective_tax
    if effective_tax is not None and (effective_tax < 0 or effective_tax > 1 or f.pretax_income < 0):
        warnings.append('The effective tax rate is unusual or based on a pretax loss. It has not been clipped; consider the 21% assumption.')
    if date.fromisoformat(f.fiscal_end) > end:
        warnings.append('The selected fiscal year ends after the return window. This matches an explicit notebook-style fiscal-year choice, but introduces look-ahead information.')
    warnings.append('Compustat period ends are not filing-availability dates; these restatable financials are not a point-in-time backtest.')
    if len(aligned) < 252:
        warnings.append('Less than one trading year of overlapping returns; beta and annualized returns may be unstable.')
    if len(stock) != len(aligned) or len(market) != len(aligned):
        warnings.append('Beta uses overlapping dates only; market return uses all valid proxy returns in the requested window.')
    if (end - date.fromisoformat(risk_free_date)).days > 7:
        warnings.append('The latest available Treasury observation is more than seven days before the window end.')
    debt_cost = f.interest / average_debt
    equity_cost = risk_free + beta * (market_return - risk_free)
    wd = f.debt / (f.debt + f.equity)
    metrics = dict(beta=beta, market_return=market_return, risk_free=risk_free,
                   cost_equity=equity_cost, cost_debt=debt_cost,
                   after_tax_debt=debt_cost * (1-tax), tax_rate=tax,
                   effective_tax_rate=effective_tax, debt_weight=wd, equity_weight=1-wd,
                   wacc=wd*debt_cost*(1-tax)+(1-wd)*equity_cost,
                   debt=f.debt, average_debt=average_debt, equity=f.equity,
                   interest=f.interest, taxes=f.taxes, pretax_income=f.pretax_income)
    if any(v is not None and not finite(v) for v in metrics.values()):
        raise CapitalError('The selected inputs produced a non-finite result. Choose another window or review the financial inputs.')
    growth = (1 + aligned).cumprod() * 100
    series = [dict(date=d.date().isoformat(), stock=float(row['stock']), market=float(row['market']),
                   stock_index=float(growth.loc[d, 'stock']), market_index=float(growth.loc[d, 'market']))
              for d, row in aligned.iterrows()]
    return dict(ticker=ticker, proxy=proxy, company=f.company, gvkey=f.gvkey,
                start=start.isoformat(), end=end.isoformat(), fiscal_year=f.fiscal_year,
                fiscal_end=f.fiscal_end, risk_free_date=risk_free_date,
                tax_mode='Corporate 21%' if corporate_tax else 'Calculated effective rate',
                observations=len(aligned), market_observations=len(market), metrics=metrics,
                warnings=warnings, series=series, units='USD millions, except returns and ratios',
                sources={'returns': 'WRDS / crsp_a_stock.dsf and dsenames',
                         'financials': 'WRDS / comp.funda (INDL, STD, D, C)',
                         'risk_free': 'FRED / DGS10'})
