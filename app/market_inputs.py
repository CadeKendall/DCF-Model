"""Market and cost-of-capital reference inputs, each cached with retrieval metadata.

Sources (no credentials beyond the existing FMP key):
- FMP /profile: latest share price, FMP's own beta and market cap (snapshot).
- U.S. Treasury daily par yield curve CSV: 10-year Treasury yield.
- Aswath Damodaran (NYU Stern) public datasets: industry betas (betas.html) and
  historical implied equity risk premiums (histimpl.xls).
- SEC companyfacts: cover-page shares outstanding (dei).
These are reference observations. Selected WACC inputs live in the assumptions file.
"""

import html
import io
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

from app.sec import PROJECT_ROOT, load_companyfacts

TREASURY_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
                "&field_tdr_date_value={year}&page&_format=csv")
DAMODARAN_BETAS = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/Betas.html"
DAMODARAN_ERP = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/histimpl.xls"


class MarketDataError(Exception):
    pass


def _cached_download(name, url, filename, *, refresh, root, flag="REFRESH_MARKET"):
    cache = Path(root) / "data/raw/market" / name
    if not refresh:
        saved = sorted(cache.glob("*/metadata.json"))
        if not saved:
            raise MarketDataError(f"No saved {name} snapshot. Set {flag} = True once and rerun this cell.")
        metadata = json.loads(saved[-1].read_text(encoding="utf-8"))
        return (saved[-1].parent / filename).read_bytes(), metadata
    try:
        with urlopen(Request(url, headers={"User-Agent": "DCF-Model research"}), timeout=90) as response:
            content = response.read()
    except HTTPError as error:
        status = error.code
        error.close()
        raise MarketDataError(f"{name}: HTTP {status}. No cache was substituted.") from None
    except (URLError, TimeoutError, OSError):
        raise MarketDataError(f"{name}: download failed. No cache was substituted.") from None
    stamp = datetime.now(timezone.utc)
    directory = cache / f"{stamp:%Y%m%dT%H%M%S%fZ}"
    directory.mkdir(parents=True, exist_ok=False)
    (directory / filename).write_bytes(content)
    metadata = {"provider": name, "url": url, "retrieved_at_utc": stamp.isoformat()}
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return content, metadata


def treasury_10y(as_of, *, refresh=False, root=PROJECT_ROOT):
    """Latest 10-year par yield on or before ``as_of`` (decimal)."""
    content, metadata = _cached_download("treasury_par_yield", TREASURY_URL.format(year=as_of.year),
                                         "yield_curve.csv", refresh=refresh, root=root)
    frame = pd.read_csv(io.BytesIO(content))
    if "10 Yr" not in frame or "Date" not in frame:
        raise MarketDataError("Treasury CSV lacks the Date or 10 Yr column.")
    frame["Date"] = pd.to_datetime(frame["Date"], format="%m/%d/%Y", errors="coerce")
    frame = frame[frame["Date"].dt.date <= as_of].dropna(subset=["Date", "10 Yr"]).sort_values("Date")
    if frame.empty:
        raise MarketDataError("No 10-year Treasury observation on or before the valuation date.")
    row = frame.iloc[-1]
    return {"value": float(row["10 Yr"]) / 100, "observation_date": row["Date"].date().isoformat(),
            "source": "U.S. Treasury daily par yield curve, 10-year", **metadata}


def _html_rows(content):
    text = content.decode("latin-1")
    rows = []
    for tr in re.findall(r"(?is)<tr.*?</tr>", text):
        cells = re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", tr)
        rows.append([re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", c))).strip() for c in cells])
    return rows, text


def industry_beta(industry="Oil/Gas (Production and Exploration)", *, refresh=False, root=PROJECT_ROOT):
    content, metadata = _cached_download("damodaran_betas", DAMODARAN_BETAS, "betas.html", refresh=refresh, root=root)
    rows, text = _html_rows(content)
    header = next((r for r in rows if r and r[0] == "Industry Name"), None)
    match = [r for r in rows if r and r[0] == industry]
    if header is None or len(match) != 1:
        raise MarketDataError(f"Industry {industry!r} not found once in Damodaran betas.")
    record = dict(zip(header, match[0]))

    def pct(value):
        return float(value.rstrip("%")) / (100 if value.endswith("%") else 1)

    plain = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text)))
    dated = re.search(r"Data used is as of ([A-Z][a-z]+ \d{4})", plain)
    return {"industry": industry, "firms": int(record["Number of firms"]),
            "levered_beta": pct(record["Beta"]), "de_ratio": pct(record["D/E Ratio"]),
            "unlevered_beta": pct(record["Unlevered beta"]),
            "unlevered_beta_cash_corrected": pct(record["Unlevered beta corrected for cash"]),
            "date_of_analysis": dated.group(1).strip() if dated else None, **metadata}


def implied_erp(*, refresh=False, root=PROJECT_ROOT):
    content, metadata = _cached_download("damodaran_implied_erp", DAMODARAN_ERP, "histimpl.xls", refresh=refresh, root=root)
    frame = pd.read_excel(io.BytesIO(content), sheet_name=0, header=None)
    header_row = frame.index[frame[0] == "Year"]
    if len(header_row) != 1:
        raise MarketDataError("Damodaran ERP workbook layout changed.")
    table = frame.iloc[header_row[0] + 1:].copy()
    table.columns = list(frame.iloc[header_row[0]])
    table = table[pd.to_numeric(table["Year"], errors="coerce").notna()]
    table["Year"] = table["Year"].astype(int)
    if "Implied ERP (FCFE)" not in table:
        raise MarketDataError("Damodaran ERP workbook lacks the Implied ERP (FCFE) column.")
    series = pd.to_numeric(table.set_index("Year")["Implied ERP (FCFE)"], errors="coerce").dropna()
    latest = int(series.index.max())
    return {"series": series, "latest_year_end": latest, "latest": float(series.loc[latest]),
            "ten_year_average": float(series.loc[latest - 9:latest].mean()), **metadata}


def fmp_profile(ticker, *, refresh=False, root=PROJECT_ROOT):
    """Latest FMP profile snapshot. Cached runs read the newest saved raw file."""
    directory = Path(root) / "data/raw/fmp" / ticker.upper()
    if refresh:
        from app.fmp import FMPClient  # Imported only for live refresh.
        FMPClient(env_path=Path(root) / ".env", raw_dir=Path(root) / "data/raw/fmp").get_profile(ticker)
    saved = sorted(directory.glob("profile_*.metadata.json"))
    if not saved:
        raise MarketDataError("No saved FMP profile. Set REFRESH_MARKET = True once and rerun this cell.")
    metadata = json.loads(saved[-1].read_text(encoding="utf-8"))
    records = json.loads((saved[-1].parent / metadata["raw_file"]).read_text(encoding="utf-8"))
    if not isinstance(records, list) or len(records) != 1 or records[0].get("symbol", "").upper() != ticker.upper():
        raise MarketDataError("Unexpected FMP profile payload.")
    record = records[0]
    return {"price": float(record["price"]), "fmp_beta": float(record["beta"]) if record.get("beta") is not None else None,
            "market_cap": float(record["marketCap"]) / 1e6 if record.get("marketCap") else None,
            "currency": record.get("currency"), "retrieved_at_utc": metadata["retrieved_at_utc"],
            "source": "FMP /stable/profile (price as of retrieval; not an exchange closing print)"}


def cover_shares(cik, ticker, *, root=PROJECT_ROOT):
    """Most recent dei:EntityCommonStockSharesOutstanding from cached companyfacts (millions)."""
    payload, path = load_companyfacts(cik, ticker, root=root)
    facts = payload["companyfacts"]["facts"].get("dei", {}).get("EntityCommonStockSharesOutstanding")
    if not facts:
        raise MarketDataError("Cover-page shares outstanding are not in companyfacts.")
    values = [v for unit in facts["units"].values() for v in unit]
    latest = max(values, key=lambda v: (v["end"], v["filed"]))
    return {"shares": latest["val"] / 1e6, "as_of": latest["end"], "form": latest["form"],
            "filed": latest["filed"], "accession": latest["accn"], "source": "SEC companyfacts dei:EntityCommonStockSharesOutstanding"}


def xbrl_duration(cik, ticker, tag, start, end, *, root=PROJECT_ROOT, namespace="us-gaap"):
    """One USD duration fact (USD millions) for an exact period, latest filing."""
    payload, _ = load_companyfacts(cik, ticker, root=root)
    facts = payload["companyfacts"]["facts"].get(namespace, {}).get(tag)
    if not facts:
        return None
    values = [v for v in facts["units"].get("USD", []) if v.get("start") == start and v.get("end") == end]
    if not values:
        return None
    latest = max(values, key=lambda v: v["filed"])
    return {"value": latest["val"] / 1e6, "form": latest["form"], "filed": latest["filed"], "accession": latest["accn"], "tag": tag}
