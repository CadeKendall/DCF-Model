"""Cache annual filing documents separately from SEC companyfacts."""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import dotenv_values

from app.sec import PROJECT_ROOT, SECError, load_companyfacts


def load_annual_filing(cik, ticker, year, *, refresh=False, root=PROJECT_ROOT):
    """Resolve a 10-K from cached submissions; download only on explicit refresh.

    Refresh companyfacts/submissions first to discover newly filed reports.
    A failed document refresh never falls back silently to an older document.
    The year denotes the calendar year of the fiscal period end, as in history.py.
    """
    payload, _ = load_companyfacts(cik, ticker, root=root)
    cik = str(cik).zfill(10)
    recent = payload["submissions"]["filings"]["recent"]
    candidates = [
        i for i, form in enumerate(recent["form"])
        if form == "10-K" and recent["reportDate"][i].startswith(f"{int(year)}-")
    ]
    if not candidates:
        raise SECError(f"No FY{year} 10-K in cached submissions. Refresh SEC metadata or review older filings.")
    i = max(candidates, key=lambda j: recent["filingDate"][j])
    report_date = recent["reportDate"][i]
    if any(form == "10-K/A" and recent["reportDate"][j] == report_date
           for j, form in enumerate(recent["form"])):
        raise SECError("An amended 10-K exists for this period; review its scope before using the original filing.")
    accession = recent["accessionNumber"][i]
    document = recent["primaryDocument"][i]
    if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession) or not re.fullmatch(r"[\w.-]+\.html?", document):
        raise SECError("Unexpected filing document identifier in SEC metadata.")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{document}"
    expected = {"cik": cik, "accession": accession, "report_date": report_date,
                "form": "10-K", "filed": recent["filingDate"][i], "url": url}
    cache = Path(root) / "data/raw/sec" / cik / "filings" / accession
    if not refresh:
        cached = sorted(cache.glob("*/metadata.json"))
        if not cached:
            raise SECError("Annual filing document is not cached. Set REFRESH_FILING = True once and rerun this section.")
        metadata_path = cached[-1]
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise SECError("Cached filing identity differs from SEC submissions metadata.")
        return (metadata_path.parent / "filing.html").read_bytes(), metadata, metadata_path.parent / "filing.html"

    settings = dotenv_values(Path(root) / ".env", interpolate=False)
    agent = os.environ.get("SEC_USER_AGENT") or settings.get("SEC_USER_AGENT", "")
    if not agent or "@" not in agent or "\n" in agent or "\r" in agent:
        raise SECError("Configure SEC_USER_AGENT in your local .env with an application name and contact email.")
    try:
        with urlopen(Request(url, headers={"User-Agent": agent, "Accept": "text/html"}), timeout=45) as response:
            document_bytes = response.read()
    except HTTPError as error:
        status = error.code
        error.close()
        raise SECError(f"SEC filing HTTP {status}. No cache was substituted.") from None
    except (URLError, TimeoutError, OSError):
        raise SECError("SEC filing download failed. No cache was substituted.") from None
    if b"inlineXBRL" not in document_bytes:
        raise SECError("SEC response is not a supported inline XBRL document; nothing was cached.")
    stamp = datetime.now(timezone.utc)
    directory = cache / f"{stamp:%Y%m%dT%H%M%S%fZ}"
    directory.mkdir(parents=True, exist_ok=False)
    metadata = {**expected, "retrieved_at_utc": stamp.isoformat()}
    path = directory / "filing.html"
    path.write_bytes(document_bytes)
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return document_bytes, metadata, path
