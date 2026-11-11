"""Load reviewed company guidance and verify it against cached SEC documents.

Two verification types:
- text: the record's ``quoted_text`` must appear in the normalized cached document.
- image: the cached page image must match the pinned SHA-256. Numbers transcribed
  from an image cannot be re-read by code; the audit labels them accordingly.
A failed check raises GuidanceError. Nothing is substituted.
"""

import hashlib
import json
import math
import re
from pathlib import Path

import pandas as pd

from app.eog_quarterly import document_text
from app.sec import PROJECT_ROOT
from app.sec_filings import load_filing_document

REQUIRED = {"id", "metric", "period", "low", "high", "point", "units", "basis", "source"}


class GuidanceError(ValueError):
    pass


def _finite_or_none(value, field, record_id):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise GuidanceError(f"{record_id}: {field} must be a finite number or null.")
    return float(value)


def validate_records(payload):
    """Schema checks that need no documents. Returns a DataFrame of records."""
    sources = payload.get("sources", {})
    records = payload.get("records", [])
    if not records:
        raise GuidanceError("No guidance records.")
    seen = set()
    rows = []
    for record in records:
        missing = REQUIRED - set(record)
        if missing:
            raise GuidanceError(f"{record.get('id', '?')}: missing fields {sorted(missing)}.")
        rid = record["id"]
        if rid in seen:
            raise GuidanceError(f"Duplicate guidance id {rid}.")
        seen.add(rid)
        source = sources.get(record["source"])
        if source is None:
            raise GuidanceError(f"{rid}: unknown source {record['source']}.")
        low, high, point = (_finite_or_none(record[k], k, rid) for k in ("low", "high", "point"))
        if low is None and high is None and point is None:
            raise GuidanceError(f"{rid}: at least one value is required.")
        if low is not None and high is not None and low > high:
            raise GuidanceError(f"{rid}: low exceeds high.")
        if point is not None and low is not None and high is not None and not low - 1e-9 <= point <= high + 1e-9:
            raise GuidanceError(f"{rid}: point is outside the printed range.")
        if source["kind"] == "text" and not record.get("quoted_text"):
            raise GuidanceError(f"{rid}: text-sourced records need quoted_text.")
        rows.append({**record, "low": low, "high": high, "point": point,
                     "accession": source["accession"], "document": source["document"],
                     "filed": source["filed"], "url": source["url"], "kind": source["kind"]})
    return pd.DataFrame(rows)


def _normalize(text):
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip()


def verify_records(frame, sources, fetch):
    """Verify each record. ``fetch(source_dict) -> (bytes, metadata)`` reads the cache."""
    status, cache = [], {}
    for _, row in frame.iterrows():
        source = sources[row["source"]]
        if row["source"] not in cache:
            cache[row["source"]] = fetch(source)
        document_bytes, metadata = cache[row["source"]]
        if source["kind"] == "image":
            digest = hashlib.sha256(document_bytes).hexdigest()
            if digest != source["sha256"]:
                raise GuidanceError(f"{row['id']}: cached image hash differs from the reviewed image.")
            status.append(("Image hash verified; values manually transcribed", metadata.get("retrieved_at_utc")))
        else:
            text = _normalize(document_text(document_bytes))
            if _normalize(row["quoted_text"]) not in text:
                raise GuidanceError(f"{row['id']}: quoted text not found in {source['document']}.")
            status.append(("Quoted text found in filing", metadata.get("retrieved_at_utc")))
    frame = frame.copy()
    frame["verification"] = [s for s, _ in status]
    frame["retrieved_at_utc"] = [r for _, r in status]
    return frame


def load_guidance(path=PROJECT_ROOT / "data/reference/eog_guidance.json", *, cik, ticker,
                  refresh=False, root=PROJECT_ROOT):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    frame = validate_records(payload)

    def fetch(source):
        document_bytes, metadata, _ = load_filing_document(
            cik, ticker, source["accession"], document=source["document"], refresh=refresh, root=root)
        return document_bytes, metadata

    return verify_records(frame, payload["sources"], fetch)


def guidance_value(frame, metric, period, field="point"):
    rows = frame[(frame.metric == metric) & (frame.period == period) & frame[field].notna()]
    if rows.empty:
        return None
    # Prefer the image table (full precision) over a text cross-check of the same metric.
    rows = rows.sort_values("kind")
    return float(rows.iloc[0][field])
