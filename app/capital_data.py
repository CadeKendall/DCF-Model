"""Read-only notebook data sources and independent ZIP-code weather."""
from datetime import timedelta
from io import StringIO
import json
import re
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError

import pandas as pd

from app.capital_model import CapitalError, Financials, finite
from app.wrds_client import WRDSClient


def fetch_text(url):
    try:
        with urlopen(Request(url, headers={'User-Agent': 'TigerCapital/1.0'}), timeout=20) as response:
            return response.read(5_000_000).decode('utf-8')
    except HTTPError as error:
        if error.code == 404:
            raise CapitalError('No data found for that location or request.') from None
        raise CapitalError('The data provider is unavailable or rate-limiting requests. Try again shortly.') from None
    except (OSError, ValueError):
        raise CapitalError('Could not reach the data provider. Check your internet connection and try again.') from None


def risk_free_at(end):
    query = urlencode({'id': 'DGS10', 'cosd': (end-timedelta(days=45)).isoformat(), 'coed': end.isoformat()})
    try:
        frame = pd.read_csv(StringIO(fetch_text('https://fred.stlouisfed.org/graph/fredgraph.csv?' + query)))
        dates = pd.to_datetime(frame.iloc[:, 0], errors='coerce')
        values = pd.to_numeric(frame['DGS10'], errors='coerce')
        valid = values.notna() & dates.notna() & (dates.dt.date <= end)
        clean = pd.DataFrame({'date': dates[valid], 'value': values[valid]}).sort_values('date')
        if clean.empty:
            raise CapitalError('No FRED 10-year Treasury observation is available near the ending date.')
        row = clean.iloc[-1]
        return float(row['value']) / 100, row['date'].date().isoformat()
    except (KeyError, IndexError, pd.errors.ParserError):
        raise CapitalError('FRED returned an unexpected response. Please retry later.') from None


class CapitalClient(WRDSClient):
    def returns(self, ticker, start, end):
        params = {'ticker': ticker, 'start': start, 'end': end}
        identities = self._query('''SELECT DISTINCT permno FROM crsp_a_stock.dsenames
            WHERE ticker = %(ticker)s AND namedt <= %(end)s AND nameendt >= %(start)s''', params)
        if len(identities) != 1:
            raise CapitalError(f'{ticker} must match exactly one CRSP security in this window. Try a shorter window or verify the ticker.')
        return self._query('''SELECT date, ret FROM crsp_a_stock.dsf
            WHERE permno = %(permno)s AND date BETWEEN %(start)s AND %(end)s ORDER BY date''',
            {**params, 'permno': int(identities.iloc[0]['permno'])})

    def financials(self, ticker, end, fiscal_year=None):
        filters = "indfmt='INDL' AND datafmt='STD' AND popsrc='D' AND consol='C'"
        identities = self._query(f'SELECT DISTINCT gvkey FROM comp.funda WHERE tic=%(ticker)s AND {filters}', {'ticker': ticker})
        if len(identities) != 1:
            raise CapitalError('Ticker must match exactly one Compustat company; verify the ticker.')
        gvkey = str(identities.iloc[0]['gvkey'])
        selector = 'fyear=%(year)s' if fiscal_year else 'datadate<=%(end)s'
        frame = self._query(f'''SELECT gvkey, conm, fyear, datadate, curcd, dltt, dlc, xint, txt, pi, prcc_f, csho
            FROM comp.funda WHERE gvkey=%(gvkey)s AND {filters} AND {selector}
            ORDER BY datadate DESC LIMIT 2''', {'gvkey': gvkey, 'end': end, 'year': fiscal_year})
        if frame.empty:
            raise CapitalError('No annual Compustat financials match the selected period.')
        row = frame.iloc[0]
        if len(frame) > 1 and (fiscal_year or frame.iloc[1]['datadate'] == row['datadate']):
            raise CapitalError('Duplicate fiscal records require review.')
        if row['curcd'] != 'USD':
            raise CapitalError('This notebook model supports USD financial statements only.')
        previous = self._query(f'''SELECT dltt, dlc, curcd FROM comp.funda WHERE gvkey=%(gvkey)s
            AND fyear=%(year)s AND {filters}''', {'gvkey': gvkey, 'year': int(row['fyear'])-1})
        if len(previous) > 1:
            raise CapitalError('Multiple previous-year debt records require review.')
        def number(value):
            return float(value) if finite(value) else None
        def debt(record):
            # Refuse a partial balance rather than treating missing debt as zero.
            a, b = number(record['dltt']), number(record['dlc'])
            return a+b if a is not None and b is not None else None
        previous_debt = debt(previous.iloc[0]) if len(previous) and previous.iloc[0]['curcd'] == 'USD' else None
        price, shares = number(row['prcc_f']), number(row['csho'])
        equity = price*shares if price is not None and shares is not None and price > 0 and shares > 0 else None
        return Financials(int(row['fyear']), pd.Timestamp(row['datadate']).date().isoformat(),
                          debt(row), previous_debt, number(row['xint']), number(row['txt']),
                          number(row['pi']), equity, str(row['conm']), gvkey)


def weather(zip_code):
    if not re.fullmatch(r'\d{5}', zip_code):
        raise CapitalError('Enter a five-digit U.S. ZIP code.')
    try:
        place = json.loads(fetch_text(f'https://api.zippopotam.us/us/{zip_code}'))['places'][0]
        query = urlencode({'latitude': place['latitude'], 'longitude': place['longitude'],
            'current': 'temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m',
            'temperature_unit': 'fahrenheit', 'wind_speed_unit': 'mph', 'timezone': 'auto', 'forecast_days': 1})
        payload = json.loads(fetch_text('https://api.open-meteo.com/v1/forecast?' + query))
        current = payload['current']
        if any(current.get(key) is None for key in ['temperature_2m', 'weather_code', 'wind_speed_10m', 'relative_humidity_2m', 'apparent_temperature', 'time']):
            raise CapitalError('Current weather is unavailable for this ZIP code.')
        code = current['weather_code']
        description = ('Clear sky' if code == 0 else 'Partly cloudy' if code in [1, 2] else
                       'Overcast' if code == 3 else 'Fog' if code in [45, 48] else
                       'Drizzle' if 51 <= code <= 57 else 'Rain' if 61 <= code <= 67 else
                       'Snow' if 71 <= code <= 77 else 'Rain showers' if 80 <= code <= 82 else
                       'Snow showers' if code in [85, 86] else 'Thunderstorms' if code >= 95 else 'Mixed conditions')
        return dict(location=f"{place['place name']}, {place['state abbreviation']}", zip=zip_code,
                    description=description, timezone=payload['timezone'], **current)
    except (ValueError, KeyError, IndexError, TypeError) as error:
        if isinstance(error, CapitalError):
            raise
        raise CapitalError('The weather provider returned incomplete data. Please retry later.') from None
