"""EIA Short-Term Energy Outlook (STEO) benchmark prices, cached as raw workbooks.

Public file, no API key: https://www.eia.gov/outlooks/steo/xls/STEO_m.xlsx
Table 2 (sheet ``2tab``) holds monthly prices. We read:
- WTIPUUS: West Texas Intermediate spot average, USD/Bbl
- NGHHMCF: Henry Hub spot, USD/Mcf
Months after the workbook's "Last Historical Month" are EIA forecasts.
Annual values are simple averages of the 12 months (all must be present).
EIA's Henry Hub *spot* series is a proxy for the NYMEX settle EOG uses for its
differentials; the audit states this.
"""

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

from app.sec import PROJECT_ROOT

STEO_URL = "https://www.eia.gov/outlooks/steo/xls/STEO_m.xlsx"
SERIES = {"WTIPUUS": ("wti", "USD/Bbl", "WTI spot average"),
          "NGHHMCF": ("henry_hub", "USD/Mcf", "Henry Hub spot")}


class EIAError(Exception):
    pass


def load_steo(*, refresh=False, root=PROJECT_ROOT):
    """Return (raw workbook bytes, metadata). Cached runs use the newest saved file."""
    cache = Path(root) / "data/raw/eia/steo"
    if not refresh:
        saved = sorted(cache.glob("*/metadata.json"))
        if not saved:
            raise EIAError("No saved STEO workbook. Set REFRESH_EIA = True once and rerun this cell.")
        metadata = json.loads(saved[-1].read_text(encoding="utf-8"))
        return (saved[-1].parent / "STEO_m.xlsx").read_bytes(), metadata
    try:
        with urlopen(Request(STEO_URL, headers={"User-Agent": "DCF-Model research"}), timeout=90) as response:
            content = response.read()
    except HTTPError as error:
        status = error.code
        error.close()
        raise EIAError(f"EIA HTTP {status}. No cache was substituted.") from None
    except (URLError, TimeoutError, OSError):
        raise EIAError("EIA download failed. No cache was substituted.") from None
    if not content.startswith(b"PK"):
        raise EIAError("EIA response is not an Excel workbook; nothing was saved.")
    stamp = datetime.now(timezone.utc)
    directory = cache / f"{stamp:%Y%m%dT%H%M%S%fZ}"
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "STEO_m.xlsx").write_bytes(content)
    metadata = {"provider": "EIA STEO", "url": STEO_URL, "retrieved_at_utc": stamp.isoformat()}
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return content, metadata


def parse_steo(content):
    """Return (monthly DataFrame, release info dict). Raises on an unexpected layout."""
    try:
        sheets = pd.read_excel(io.BytesIO(content), sheet_name=["Dates", "2tab"], header=None)
    except (ValueError, KeyError) as error:
        raise EIAError(f"Unexpected STEO workbook layout: {error}") from None
    dates, table = sheets["Dates"], sheets["2tab"]

    def date_setting(label):
        rows = dates[dates[0].astype(str).str.startswith(label)]
        if rows.empty:
            raise EIAError(f"STEO 'Dates' sheet lacks {label!r}.")
        return rows.iloc[0, 3]

    begin = int(date_setting("Table Beginning Month"))
    last_historical = int(date_setting("Last Historical Month"))
    forecast_month = str(date_setting("Forecast Month"))
    start = pd.Period(f"{begin // 100}-{begin % 100:02d}", freq="M")
    out = {}
    for code, (name, _, _) in SERIES.items():
        rows = table[table[0] == code]
        if len(rows) != 1:
            raise EIAError(f"STEO series {code} not found exactly once in Table 2.")
        values = pd.to_numeric(rows.iloc[0, 2:], errors="coerce").dropna()
        months = pd.period_range(start, periods=len(values), freq="M")
        out[name] = pd.Series(values.to_numpy(dtype=float), index=months)
    monthly = pd.DataFrame(out)
    last = pd.Period(f"{last_historical // 100}-{last_historical % 100:02d}", freq="M")
    monthly["status"] = ["EIA historical" if m <= last else "EIA forecast" for m in monthly.index]
    info = {"release": forecast_month, "last_historical_month": str(last)}
    return monthly, info


def annual_prices(monthly):
    """Simple 12-month averages; a year with fewer than 12 months is left out."""
    frame = monthly.copy()
    frame["year"] = frame.index.year
    rows = []
    for year, group in frame.groupby("year"):
        if len(group) != 12:
            continue
        forecast_months = int((group.status == "EIA forecast").sum())
        rows.append({"year": int(year), "wti": group.wti.mean(), "henry_hub": group.henry_hub.mean(),
                     "forecast_months": forecast_months,
                     "status": "EIA historical" if forecast_months == 0 else
                     "EIA forecast" if forecast_months == 12 else f"EIA mixed ({12 - forecast_months} historical months)"})
    return pd.DataFrame(rows).set_index("year")


def quarter_prices(monthly, year, quarter):
    months = [pd.Period(f"{year}-{m:02d}", freq="M") for m in range(3 * quarter - 2, 3 * quarter + 1)]
    part = monthly.reindex(months)
    if part[["wti", "henry_hub"]].isna().any().any():
        raise EIAError(f"STEO lacks monthly prices for {year}Q{quarter}.")
    return {"wti": part.wti.mean(), "henry_hub": part.henry_hub.mean(),
            "status": ", ".join(sorted(set(part.status)))}
