r"""Run: .\.venv-wrds\Scripts\python.exe capital_app.py [--preview]."""
import argparse
from datetime import date, datetime, timezone
from pathlib import Path
import threading
from typing import Literal
import webbrowser

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.capital_data import CapitalClient, risk_free_at, weather
from app.capital_model import CapitalError, calculate
from app.wrds_client import WRDSError

ROOT = Path(__file__).resolve().parent
app = FastAPI(title='Tiger Capital', docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])
app.mount('/static', StaticFiles(directory=ROOT/'app'/'capital_static'), name='static')
client = None
query_lock = threading.Lock()


class AnalysisRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=15, pattern=r'^[A-Z0-9][A-Z0-9.-]*$')
    proxy: Literal['SPY', 'QQQ', 'VOO'] = 'SPY'
    start: date
    end: date
    fiscal_year: int | None = Field(default=None, ge=1950, le=2100)
    corporate_tax: bool = False

    @field_validator('ticker', mode='before')
    @classmethod
    def normalize_ticker(cls, value):
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode='after')
    def dates_valid(self):
        if self.start >= self.end:
            raise ValueError('Beginning date must be before ending date.')
        if self.end > date.today():
            raise ValueError('Ending date cannot be in the future.')
        if self.start < date(1962, 1, 2):
            raise ValueError('Choose a beginning date on or after January 2, 1962.')
        return self


@app.get('/')
def home():
    return FileResponse(ROOT/'app'/'capital_static'/'index.html')


@app.get('/api/status')
def status():
    return {'connected': client is not None, 'today': date.today().isoformat()}


@app.post('/api/analyze')
def analyze(request: AnalysisRequest):
    if client is None:
        raise HTTPException(503, 'WRDS is not connected. In PowerShell, run .\\.venv-wrds\\Scripts\\python.exe capital_app.py and complete the WRDS / Duo sign-in.')
    if not query_lock.acquire(blocking=False):
        raise HTTPException(409, 'An analysis is already running. Wait for it to finish.')
    try:
        stock = client.returns(request.ticker, request.start, request.end)
        market = client.returns(request.proxy, request.start, request.end)
        financials = client.financials(request.ticker, request.end, request.fiscal_year)
        rf, rf_date = risk_free_at(request.end)
        result = calculate(stock, market, financials, rf, rf_date, request.ticker, request.proxy,
                           request.start, request.end, request.corporate_tax)
        result['retrieved_at'] = datetime.now(timezone.utc).isoformat()
        return result
    except (CapitalError, WRDSError) as error:
        raise HTTPException(422, str(error)) from None
    except Exception:
        raise HTTPException(502, 'Data retrieval failed. Check WRDS access to CRSP and Compustat and retry. Restart the app if your session expired.') from None
    finally:
        query_lock.release()


@app.get('/api/weather/{zip_code}')
def local_weather(zip_code: str):
    try:
        return weather(zip_code)
    except CapitalError as error:
        raise HTTPException(422, str(error)) from None


def main():
    global client
    parser = argparse.ArgumentParser(description='Clemson-inspired cost-of-capital dashboard')
    parser.add_argument('--preview', action='store_true', help='Open the UI and weather without signing into WRDS; financial results remain empty.')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if not args.preview:
        candidate = CapitalClient()
        try:
            print('Sign into WRDS below and approve Duo if prompted. Credentials stay in the terminal.')
            candidate.connect()
            client = candidate
        except WRDSError as error:
            candidate.close()
            print(f'{error}\nStarting with financial analysis disconnected; weather remains available.')
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f'http://127.0.0.1:{args.port}')).start()
    import uvicorn
    try:
        uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')
    finally:
        if client is not None:
            client.close()


if __name__ == '__main__':
    main()
