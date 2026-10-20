"""Offline tests: every price/financial input here is synthetic test data."""
from dataclasses import replace
from datetime import date
import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import capital_app
from app import capital_data
from app.capital_model import CapitalError, Financials, calculate
from app.wrds_client import WRDSError


def sample_frames():
    dates = pd.bdate_range('2024-01-01', periods=40)
    market = pd.DataFrame({'date': dates, 'ret': [.01, -.005] * 20})
    stock = market.assign(ret=market.ret*2)
    return stock, market


def sample_financials():
    return Financials(2023, '2023-12-31', 100., 80., 9., 20., 100., 400., 'SYNTHETIC TEST COMPANY', 'TEST')


def run_model(f=None, corporate=False, stock=None, market=None):
    default_stock, default_market = sample_frames()
    return calculate(default_stock if stock is None else stock, default_market if market is None else market,
                     f or sample_financials(), .04, '2024-03-01', 'TEST', 'SPY',
                     date(2024, 1, 1), date(2024, 3, 1), corporate)


def test_notebook_formulas_and_tax_override():
    calculated, corporate = run_model(), run_model(corporate=True)
    m = calculated['metrics']
    expected_market = ((1.01*.995)**20)**(252/40)-1
    expected_equity = .04+2*(expected_market-.04)
    assert m['beta'] == pytest.approx(2)
    assert m['market_return'] == pytest.approx(expected_market)
    assert m['average_debt'] == 90
    assert m['cost_debt'] == pytest.approx(.1)
    assert m['cost_equity'] == pytest.approx(expected_equity)
    assert m['debt_weight'] == pytest.approx(.2)
    assert m['wacc'] == pytest.approx(.8*expected_equity+.2*.1*.8)
    assert corporate['metrics']['wacc'] == pytest.approx(m['wacc']-.2*.1*.01)
    assert corporate['metrics']['tax_rate'] == .21
    assert corporate['metrics']['effective_tax_rate'] == .2
    assert calculated['series'][-1]['market_index'] == pytest.approx(100*(1.01*.995)**20)
    json.dumps(calculated, allow_nan=False)


def test_beta_aligns_dates_but_market_return_uses_full_proxy_window():
    stock, market = sample_frames()
    result = run_model(stock=stock.iloc[5:], market=market)
    assert result['observations'] == 35
    assert result['market_observations'] == 40
    assert result['metrics']['market_return'] == run_model()['metrics']['market_return']
    assert any('overlapping dates' in w for w in result['warnings'])


@pytest.mark.parametrize('change', [dict(debt=None), dict(interest=None), dict(equity=0), dict(debt=-1), dict(previous_debt=-1)])
def test_missing_or_invalid_financials_rejected(change):
    with pytest.raises(CapitalError):
        run_model(replace(sample_financials(), **change))


def test_missing_tax_can_be_overridden():
    for change in [dict(taxes=None), dict(pretax_income=0), dict(pretax_income=None)]:
        f = replace(sample_financials(), **change)
        with pytest.raises(CapitalError, match='Effective tax rate'):
            run_model(f)
        assert run_model(f, corporate=True)['metrics']['tax_rate'] == .21


def test_negative_effective_rate_not_silently_clipped():
    result = run_model(replace(sample_financials(), taxes=-20))
    assert result['metrics']['tax_rate'] == -.2
    assert any('unusual' in w for w in result['warnings'])


def test_debt_fallback_and_lookahead_disclosed():
    result = run_model(replace(sample_financials(), previous_debt=None, fiscal_end='2025-01-31'))
    assert result['metrics']['cost_debt'] == .09
    assert any('Previous-year' in w for w in result['warnings'])
    assert any('look-ahead' in w for w in result['warnings'])


def test_zero_average_debt_rejected():
    with pytest.raises(CapitalError, match='Average debt is zero'):
        run_model(replace(sample_financials(), debt=0, previous_debt=0))


def test_bad_or_short_return_series_rejected():
    stock, market = sample_frames()
    with pytest.raises(CapitalError, match='30 overlapping'):
        run_model(stock=stock.iloc[:10])
    with pytest.raises(CapitalError, match='Multiple returns'):
        run_model(stock=pd.concat([stock, stock.iloc[:1]]))
    with pytest.raises(CapitalError, match='variance'):
        run_model(market=market.assign(ret=0))
    with pytest.raises(CapitalError, match='below -100%'):
        run_model(market=market.assign(ret=-1.5))


def test_crsp_missing_codes_drop_without_becoming_zero():
    stock, _ = sample_frames()
    stock['ret'] = stock['ret'].astype(object)
    stock.loc[0,'ret'] = 'B'
    stock.loc[1,'ret'] = np.inf
    result = run_model(stock=stock)
    assert result['observations'] == 38


class FixtureProvider:
    """For automated verification only. Never used by the production launcher."""
    def returns(self, ticker, start, end):
        stock, market = sample_frames()
        return market if ticker in ['SPY', 'QQQ', 'VOO'] else stock

    def financials(self, ticker, end, fiscal_year):
        return sample_financials()


@pytest.fixture
def browser_api(monkeypatch):
    monkeypatch.setattr(capital_app, 'client', FixtureProvider())
    monkeypatch.setattr(capital_app, 'risk_free_at', lambda end: (.04, '2024-03-01'))
    with TestClient(capital_app.app) as client:
        yield client


REQUEST = {'ticker':' test ', 'start':'2024-01-01','end':'2024-03-01'}


@pytest.mark.parametrize('proxy', ['SPY','QQQ','VOO'])
def test_api_returns_complete_analysis_for_each_proxy(browser_api, proxy):
    response = browser_api.post('/api/analyze', json={**REQUEST, 'proxy':proxy, 'corporate_tax':True})
    assert response.status_code == 200
    result = response.json()
    assert result['ticker'] == 'TEST'
    assert result['proxy'] == proxy
    assert result['metrics']['tax_rate'] == .21
    assert result['observations'] == 40
    assert 'retrieved_at' in result


@pytest.mark.parametrize('change', [{'proxy':'DIA'}, {'ticker':"X';DROP"}, {'end':'2020-01-01'}, {'end':'2099-01-01'}, {'start':'1900-01-01'}, {'fiscal_year':1}])
def test_api_rejects_invalid_inputs(browser_api, change):
    assert browser_api.post('/api/analyze',json={**REQUEST, **change}).status_code == 422


def test_disconnect_and_query_errors_are_actionable(browser_api, monkeypatch):
    monkeypatch.setattr(capital_app, 'client', None)
    assert browser_api.get('/api/status').json()['connected'] is False
    response = browser_api.post('/api/analyze', json=REQUEST)
    assert response.status_code == 503
    assert 'PowerShell' in response.json()['detail']
    provider = FixtureProvider()
    def fail(*args):
        raise RuntimeError('secret driver connection details')
    provider.returns = fail
    monkeypatch.setattr(capital_app, 'client', provider)
    response = browser_api.post('/api/analyze', json=REQUEST)
    assert response.status_code == 502
    assert 'secret' not in response.text
    assert not capital_app.query_lock.locked()


def test_server_serves_assets_and_rejects_foreign_host(browser_api):
    assert 'Tiger Capital' in browser_api.get('/').text
    for asset in ['style.css','app.js','paw.svg']:
        assert browser_api.get('/static/'+asset).status_code == 200
    assert browser_api.get('/',headers={'host':'attacker.example'}).status_code == 400


def test_fred_uses_last_valid_observation_before_end(monkeypatch):
    monkeypatch.setattr(capital_data, 'fetch_text', lambda url: 'observation_date,DGS10\n2024-02-28,4.2\n2024-02-29,.\n2024-03-02,4.9\n')
    assert capital_data.risk_free_at(date(2024,3,1)) == (.042, '2024-02-28')


def test_weather_is_independent_and_preserves_leading_zero_zip(browser_api, monkeypatch):
    urls=[]
    def fetch(url):
        urls.append(url)
        if 'zippopotam' in url:
            return json.dumps({'places':[{'place name':'Test City','state abbreviation':'MA','latitude':'42','longitude':'-71'}]})
        return json.dumps({'timezone':'America/New_York','current':{'temperature_2m':70,'apparent_temperature':69,
            'relative_humidity_2m':50,'weather_code':0,'wind_speed_10m':5,'time':'2024-03-01T12:00'}})
    monkeypatch.setattr(capital_data,'fetch_text',fetch)
    monkeypatch.setattr(capital_app,'client',None)
    response=browser_api.get('/api/weather/02108')
    assert response.status_code == 200
    assert response.json()['temperature_2m'] == 70
    assert urls[0].endswith('/02108')
    assert browser_api.get('/api/weather/123').status_code == 422


def test_incomplete_weather_is_an_error(monkeypatch):
    monkeypatch.setattr(capital_data,'fetch_text',lambda url:'{}')
    with pytest.raises(CapitalError,match='incomplete'):
        capital_data.weather('29631')


def test_crsp_query_is_parameterized_and_identity_is_unique(monkeypatch):
    provider=capital_data.CapitalClient()
    calls=[]
    def query(sql,params):
        calls.append((sql,params))
        if 'DISTINCT' in sql:
            return pd.DataFrame({'permno':[123]})
        return sample_frames()[0]
    monkeypatch.setattr(provider,'_query',query)
    provider.returns('TEST',date(2024,1,1),date(2024,3,1))
    assert calls[0][1]['ticker'] == 'TEST'
    assert 'TEST' not in calls[0][0]
    assert calls[1][1]['permno'] == 123
    monkeypatch.setattr(provider,'_query',lambda *args:pd.DataFrame({'permno':[1,2]}))
    with pytest.raises(CapitalError,match='exactly one'):
        provider.returns('TEST',date(2024,1,1),date(2024,3,1))


def test_compustat_selection_and_missing_debt_are_not_fabricated(monkeypatch):
    provider=capital_data.CapitalClient()
    row=dict(gvkey='123',conm='Test',fyear=2023,datadate='2023-12-31',curcd='USD',dltt=80,dlc=20,xint=9,txt=20,pi=100,prcc_f=4,csho=100)
    calls=[]
    def query(sql,params):
        calls.append((sql,params))
        if 'DISTINCT' in sql:return pd.DataFrame({'gvkey':['123']})
        if 'SELECT dltt' in sql:return pd.DataFrame([dict(dltt=60,dlc=20,curcd='USD')])
        return pd.DataFrame([row])
    monkeypatch.setattr(provider,'_query',query)
    f=provider.financials('TEST',date(2024,3,1))
    assert f.debt == 100 and f.previous_debt == 80 and f.equity == 400
    assert 'datadate<=%(end)s' in calls[1][0]
    calls.clear()
    provider.financials('TEST',date(2024,3,1),2023)
    assert 'fyear=%(year)s' in calls[1][0]
    row['dlc']=None
    assert provider.financials('TEST',date(2024,3,1)).debt is None
