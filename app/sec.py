"""SEC public company facts with explicit refresh and raw-response caching."""

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SECError(Exception):
    pass


def load_companyfacts(cik, ticker, *, refresh=False, root=PROJECT_ROOT):
    """No network on cached runs. A failed explicit refresh never uses stale data."""
    cik = str(cik).strip()
    if not re.fullmatch(r"\d{1,10}", cik):
        raise SECError("CIK must contain 1 to 10 digits.")
    cik = cik.zfill(10)
    cache = Path(root) / "data" / "raw" / "sec" / cik
    if not refresh:
        files = sorted(cache.glob("*/companyfacts.json"))
        if not files:
            raise SECError("No SEC cache. Configure SEC_USER_AGENT in .env, then set REFRESH_SEC = True.")
        path = files[-1]
        payload = json.loads(path.read_text(encoding="utf-8"))
        _validate(payload, cik, ticker)
        return payload, path

    settings = dotenv_values(Path(root) / ".env", interpolate=False)
    agent = os.environ.get("SEC_USER_AGENT") or settings.get("SEC_USER_AGENT", "")
    if not agent or "@" not in agent or "\n" in agent or "\r" in agent:
        raise SECError("Set SEC_USER_AGENT in .env to an application name and your contact email.")

    def fetch(url):
        request = Request(url, headers={"User-Agent": agent, "Accept": "application/json"})
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            status = error.code
            error.close()
            raise SECError(f"SEC HTTP {status}. Check User-Agent; retry later for 429/503.") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise SECError("SEC download failed or returned invalid JSON. No cache was substituted.") from None

    submissions = fetch(f"https://data.sec.gov/submissions/CIK{cik}.json")
    time.sleep(0.25)  # Below SEC's maximum request rate.
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    facts = fetch(url)
    stamp = datetime.now(timezone.utc)
    payload = {"provider": "SEC", "retrieved_at_utc": stamp.isoformat(),
               "url": url, "submissions": submissions, "companyfacts": facts}
    _validate(payload, cik, ticker)
    directory = cache / f"{stamp:%Y%m%dT%H%M%S%fZ}_{uuid4().hex[:8]}"
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / "companyfacts.json"
    path.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    return payload, path


def _validate(payload, cik, ticker):
    if not isinstance(payload, dict):
        raise SECError("Invalid SEC cache structure.")
    facts = payload.get("companyfacts", {})
    submissions = payload.get("submissions", {})
    if not isinstance(facts, dict) or not isinstance(submissions, dict):
        raise SECError("SEC response is not a company-facts/submissions object.")
    if str(facts.get("cik", "")).zfill(10) != cik or str(submissions.get("cik", "")).zfill(10) != cik:
        raise SECError("SEC response identity does not match the requested CIK.")
    if ticker.upper() not in [x.upper() for x in submissions.get("tickers", [])]:
        raise SECError("Ticker does not match this CIK's SEC submissions record.")
    if not isinstance(facts.get("facts", {}).get("us-gaap"), dict):
        raise SECError("SEC response has no US-GAAP facts.")
