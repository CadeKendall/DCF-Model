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


def _agent(root):
    settings = dotenv_values(Path(root) / ".env", interpolate=False)
    agent = os.environ.get("SEC_USER_AGENT") or settings.get("SEC_USER_AGENT", "")
    if not agent or "@" not in agent or "\n" in agent or "\r" in agent:
        raise SECError("Configure SEC_USER_AGENT in your local .env with an application name and contact email.")
    return agent


def _fetch(url, agent, accept="text/html"):
    try:
        with urlopen(Request(url, headers={"User-Agent": agent, "Accept": accept}), timeout=45) as response:
            return response.read()
    except HTTPError as error:
        status = error.code
        error.close()
        raise SECError(f"SEC filing HTTP {status}. No cache was substituted.") from None
    except (URLError, TimeoutError, OSError):
        raise SECError("SEC filing download failed. No cache was substituted.") from None


def _cached_or_download(cache, expected, *, refresh, root, require_inline_xbrl=True, refresh_flag="REFRESH_FILING"):
    """Shared cache logic. Cached runs need no network; a failed refresh never falls back."""
    if not refresh:
        cached = sorted(cache.glob("*/metadata.json"))
        if not cached:
            raise SECError(f"Filing document is not cached. Set {refresh_flag} = True once and rerun this section.")
        metadata_path = cached[-1]
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise SECError("Cached filing identity differs from SEC submissions metadata.")
        name = metadata.get("cached_file", "filing.html")
        return (metadata_path.parent / name).read_bytes(), metadata, metadata_path.parent / name

    document_bytes = _fetch(expected["url"], _agent(root))
    if require_inline_xbrl and b"inlineXBRL" not in document_bytes:
        raise SECError("SEC response is not a supported inline XBRL document; nothing was cached.")
    is_image = expected["url"].lower().endswith((".jpg", ".jpeg"))
    if is_image and not document_bytes.startswith(b"\xff\xd8"):
        raise SECError("SEC response is not a JPEG image; nothing was cached.")
    if not is_image and not require_inline_xbrl and b"<" not in document_bytes[:4096]:
        raise SECError("SEC response is not an HTML document; nothing was cached.")
    stamp = datetime.now(timezone.utc)
    directory = cache / f"{stamp:%Y%m%dT%H%M%S%fZ}"
    directory.mkdir(parents=True, exist_ok=False)
    name = "document.jpg" if is_image else "filing.html"
    metadata = {**expected, "retrieved_at_utc": stamp.isoformat(), "cached_file": name}
    path = directory / name
    path.write_bytes(document_bytes)
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return document_bytes, metadata, path


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
    return _cached_or_download(cache, expected, refresh=refresh, root=root)


def load_filing_document(cik, ticker, accession, *, document=None, refresh=False, root=PROJECT_ROOT):
    """Load a filing's primary document, a named exhibit or an exhibit page image.

    The accession must appear in the cached SEC submissions record. ``document`` is
    the file name inside the filing folder; None selects the primary document.
    Exhibits are plain HTML (or JPEG page images), so the inline-XBRL check applies
    to primary 10-K/10-Q documents only. Each document is cached in its own folder under the accession.
    """
    payload, _ = load_companyfacts(cik, ticker, root=root)
    cik = str(cik).zfill(10)
    recent = payload["submissions"]["filings"]["recent"]
    if accession not in recent["accessionNumber"]:
        raise SECError(f"Accession {accession} is not in cached SEC submissions. Refresh SEC metadata first.")
    i = recent["accessionNumber"].index(accession)
    primary = recent["primaryDocument"][i]
    document = document or primary
    if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession) or not re.fullmatch(r"[\w.-]+\.(html?|jpe?g)", document):
        raise SECError("Unexpected filing document identifier.")
    form = recent["form"][i]
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{document}"
    expected = {"cik": cik, "accession": accession, "report_date": recent["reportDate"][i],
                "form": form, "filed": recent["filingDate"][i], "url": url}
    is_primary_report = document == primary and form in {"10-K", "10-Q", "10-K/A", "10-Q/A"}
    cache = Path(root) / "data/raw/sec" / cik / "filings" / accession
    if document != primary:
        cache = cache / "documents" / document.replace(".", "_")
    return _cached_or_download(cache, expected, refresh=refresh, root=root,
                               require_inline_xbrl=is_primary_report, refresh_flag="REFRESH_GUIDANCE")


def list_filing_exhibits(cik, accession, *, root=PROJECT_ROOT):
    """Return the filing index (document names and types) from SEC; network call."""
    cik = str(cik).zfill(10)
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/index.json"
    payload = json.loads(_fetch(url, _agent(root), accept="application/json"))
    return [item["name"] for item in payload.get("directory", {}).get("item", [])]
