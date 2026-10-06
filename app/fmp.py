"""Centralized FMP connectivity. No financial normalization or calculations."""

import json
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, quote_plus, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://financialmodelingprep.com/stable"


class FMPError(Exception):
    """Safe, user-facing error without request URLs or provider response bodies."""


class FMPConfigurationError(FMPError):
    """The local API key is missing or unreadable."""


class FMPInvalidTickerError(FMPError):
    """The ticker has an unsupported format."""


class FMPAPIError(FMPError):
    """FMP rejected a request or returned an error payload."""


class FMPEmptyResponseError(FMPError):
    """No records were available; this can indicate an unknown ticker."""


class FMPResponseError(FMPError):
    """The response was malformed or did not match the request."""


class FMPNetworkError(FMPError):
    """The request timed out or could not reach FMP."""


class FMPStorageError(FMPError):
    """The successful raw response could not be saved locally."""


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a credential-bearing request to another destination.
        return None


def _reject_json_constant(value: str) -> None:
    # Python otherwise accepts NaN/Infinity, which are not valid JSON values.
    raise ValueError("Nonstandard JSON constant")


@dataclass
class FMPResponse:
    """Unmodified provider records plus paths to raw data and source metadata."""

    records: list[dict] = field(repr=False)
    raw_path: Path
    metadata_path: Path


class FMPClient:
    def __init__(
        self,
        env_path: Path = PROJECT_ROOT / ".env",
        raw_dir: Path = PROJECT_ROOT / "data" / "raw" / "fmp",
        timeout: float = 20,
    ) -> None:
        # Resolve the project file explicitly, even when run from another folder.
        # Disable interpolation so key characters are never substituted.
        try:
            values = dotenv_values(env_path, encoding="utf-8-sig", interpolate=False)
        except (OSError, UnicodeError):
            raise FMPConfigurationError("Cannot read the local .env file.") from None
        self._api_key = (values.get("FMP_API_KEY") or "").strip()
        if not self._api_key:
            raise FMPConfigurationError(
                "FMP_API_KEY is missing. Enter it in the project's local .env file. "
                "Do not paste the key into chat or source code."
            )
        self._raw_dir = Path(raw_dir)
        self._timeout = timeout
        # urllib's default HTTPS handler validates certificates and does not log
        # request URLs. Redirects are refused; there are no automatic retries.
        self._opener = build_opener(_NoRedirects())

    def get_profile(self, ticker: str) -> FMPResponse:
        return self._request("profile", ticker)

    def get_quote(self, ticker: str) -> FMPResponse:
        return self._request("quote", ticker)

    def get_income_statement(self, ticker: str, limit: int = 5) -> FMPResponse:
        return self._annual("income-statement", ticker, limit)

    def get_balance_sheet(self, ticker: str, limit: int = 5) -> FMPResponse:
        return self._annual("balance-sheet-statement", ticker, limit)

    def get_cash_flow(self, ticker: str, limit: int = 5) -> FMPResponse:
        return self._annual("cash-flow-statement", ticker, limit)

    def _annual(self, endpoint: str, ticker: str, limit: int) -> FMPResponse:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise FMPError("Statement limit must be an integer from 1 to 100.")
        return self._request(endpoint, ticker, period="annual", limit=limit)

    def _request(self, endpoint: str, ticker: str, **options) -> FMPResponse:
        if not isinstance(ticker, str):
            raise FMPInvalidTickerError("Ticker must be a string such as EOG.")
        ticker = ticker.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*", ticker) or len(ticker) > 20:
            raise FMPInvalidTickerError("Invalid ticker format. Use a symbol such as EOG.")
        parameters = {"symbol": ticker, **options}
        query = urlencode({**parameters, "apikey": self._api_key})
        request = Request(
            f"{BASE_URL}/{endpoint}?{query}",
            headers={"Accept": "application/json", "User-Agent": "DCF-Model/Phase-1"},
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                status = response.status
                raw = response.read()
        except HTTPError as error:
            status = error.code
            error.close()
            raise FMPAPIError(self._http_message(status)) from None
        except (TimeoutError, socket.timeout):
            raise FMPNetworkError("FMP request timed out. Try again later.") from None
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise FMPNetworkError("FMP request timed out. Try again later.") from None
            raise FMPNetworkError(
                "Could not reach FMP. Check the network connection and try again."
            ) from None
        except (OSError, HTTPException):
            raise FMPNetworkError(
                "Could not reach FMP. Check the network connection and try again."
            ) from None

        if not 200 <= status < 300:
            raise FMPAPIError(self._http_message(status))
        if not raw.strip():
            raise FMPEmptyResponseError("FMP returned an empty response body.")
        try:
            records = json.loads(raw, parse_constant=_reject_json_constant)
        except (ValueError, UnicodeError):
            raise FMPResponseError("FMP returned malformed JSON.") from None

        # Never expose or archive a provider payload that echoes the credential.
        variants = {self._api_key, quote(self._api_key, safe=""), quote_plus(self._api_key)}
        serialized = json.dumps(records, ensure_ascii=False)
        if any(secret in serialized for secret in variants):
            raise FMPResponseError("FMP returned sensitive content; response withheld.")
        error_fields = {"error", "error message", "errormessage", "message"}
        objects = records if isinstance(records, list) else [records]
        if any(
            isinstance(item, dict) and any(str(key).lower() in error_fields for key in item)
            for item in objects
        ):
            raise FMPAPIError(
                "FMP returned an API error. Check the key, endpoint access, and usage limits."
            )
        if records is None or records == [] or records == {}:
            raise FMPEmptyResponseError(
                "FMP returned no records. The ticker may be unknown or data unavailable."
            )
        if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
            raise FMPResponseError("Expected a JSON list of FMP records.")
        if any(item.get("symbol") != ticker for item in records):
            raise FMPResponseError("FMP returned a missing or unexpected ticker.")
        if options.get("period") == "annual" and any(
            item.get("period") not in ("FY", "annual") for item in records
        ):
            raise FMPResponseError("FMP did not confirm annual periods for every statement.")

        return self._save_raw(endpoint, parameters, raw, records)

    @staticmethod
    def _http_message(status: int) -> str:
        explanations = {
            401: "Authentication failed. Check the key in .env.",
            402: "This endpoint may require a different FMP subscription.",
            403: "Access denied. Check the key and FMP endpoint permissions.",
            404: "Endpoint or ticker not found.",
            429: "Rate limit reached. Wait before trying again.",
        }
        explanation = explanations.get(status, "FMP could not complete the request.")
        return f"FMP HTTP {status}: {explanation}"

    def _save_raw(self, endpoint: str, parameters: dict, raw: bytes, records: list) -> FMPResponse:
        retrieved_at = datetime.now(timezone.utc)
        stem = f"{endpoint}_{retrieved_at:%Y%m%dT%H%M%S%fZ}_{uuid4().hex[:8]}"
        directory = self._raw_dir / parameters["symbol"]
        raw_path = directory / f"{stem}.json"
        metadata_path = directory / f"{stem}.metadata.json"
        metadata = {
            "provider": "FMP",
            "endpoint": f"{BASE_URL}/{endpoint}",
            "parameters": parameters,
            "retrieved_at_utc": retrieved_at.isoformat(),
            "raw_file": raw_path.name,
        }
        try:
            directory.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(raw)
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        except OSError:
            raise FMPStorageError(
                "Data returned, but raw files could not be saved. Check folder permissions."
            ) from None
        return FMPResponse(records, raw_path, metadata_path)
