"""Read-only WRDS discovery and raw Compustat samples; no normalized model."""

import contextlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
import wrds


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "wrds"
FIELDS = (
    "gvkey", "tic", "conm", "datadate", "fyear", "fyr", "curcd",
    "indfmt", "datafmt", "consol", "popsrc", "sale", "revt", "oiadp",
    "oibdp", "ni", "che", "at", "dlc", "dltt", "ppent", "dp", "dpc",
    "capx", "oancf", "csho", "cshfd",
)
FILTERS = {"indfmt": "INDL", "datafmt": "STD", "consol": "C", "popsrc": "D"}
RELEVANT_PREFIXES = (
    "comp", "crsp", "ciq", "capiq", "capitaliq", "factset", "worldscope",
    "tr_", "refinitiv", "wrdssec", "sec", "audit", "zacks", "wrdsapps",
)


class WRDSError(Exception):
    """Safe message that excludes credentials and underlying driver diagnostics."""


class WRDSAccessError(WRDSError):
    """An account lacks permission to read an object."""


def _identifier(name: str) -> str:
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name):
        raise WRDSError("Unexpected SQL identifier; query was not sent.")
    return f'"{name}"'


def _records(frame: pd.DataFrame) -> list[dict]:
    # Preserve provider field names and values. SQL NULL becomes JSON null;
    # Decimal/date values are serialized as strings when writing JSON below.
    return [
        {name: None if pd.isna(value) else value for name, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]


class WRDSClient:
    def __init__(self) -> None:
        self._connection = None

    def connect(self) -> None:
        if not sys.stdin.isatty():
            raise WRDSError(
                "WRDS sign-in requires your local interactive PowerShell window. "
                r"Run .\.venv-wrds\Scripts\python.exe -m app.verify_wrds there."
            )
        try:
            # Official WRDS prompts/getpass, standard PostgreSQL password-file
            # support, and Duo. Never put credentials into application settings.
            self._connection = wrds.Connection(
                verbose=False,
                wrds_hostname="wrds-pgdata.wharton.upenn.edu",
                wrds_connect_args={
                    "sslmode": "require",
                    "connect_timeout": 30,
                    "options": "-c statement_timeout=30000 -c default_transaction_read_only=on",
                    "application_name": "DCF-Model-Phase1B",
                },
            )
        except Exception:
            raise WRDSError(
                "WRDS sign-in or connection failed. Check your WRDS login, approve "
                "the Duo push, and check network access. Driver details were withheld."
            ) from None

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None

    def _call(self, method: str, *args, **kwargs):
        if self._connection is None:
            raise WRDSError("Connect to WRDS before querying.")
        try:
            # Third-party SQL diagnostics can contain connection information.
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return getattr(self._connection, method)(*args, **kwargs)
        except Exception as error:
            if isinstance(error, PermissionError) or getattr(getattr(error, "orig", None), "pgcode", None) == "42501":
                raise WRDSAccessError("WRDS denied access to this object.") from None
            raise WRDSError("WRDS query failed; connection/SQL diagnostics withheld.") from None

    def _query(self, sql: str, params: dict | None = None) -> pd.DataFrame:
        return self._call("raw_sql", sql, params=params, coerce_float=False)

    def inspect_account(self) -> dict:
        libraries = sorted(set(self._call("list_libraries")))
        relevant = [name for name in libraries if name.startswith(RELEVANT_PREFIXES)]
        sec = [name for name in libraries if name.startswith(("wrdssec", "sec"))]
        comp = [name for name in libraries if name.startswith("comp")]
        table_lists, errors = {}, {}
        for library in sorted(set(relevant + sec)):
            try:
                table_lists[library] = sorted(set(self._call("list_tables", library=library)))
            except WRDSError as error:
                errors[library] = str(error)
        return {
            "authentication": "succeeded",
            "libraries_listed": True,
            "available_libraries": libraries,
            "relevant_libraries": relevant,
            "sec_related_schema_candidates": sec,
            "compustat_schema_candidates": comp,
            "tables_by_library": table_lists,
            "catalog_errors": errors,
            "sec_scope": "Schema/table names only; no SEC records retrieved.",
        }

    def _columns(self, schema: str, table: str) -> pd.DataFrame:
        return self._call("describe_table", library=schema, table=table)

    def _annual_sample(self, schema: str, tables: list[str], ticker: str, limit: int) -> dict:
        description = self._columns(schema, "funda")
        columns = set(description["name"])
        required = {"gvkey", "datadate", "fyear", *FILTERS}
        if not required.issubset(columns):
            raise WRDSError("Annual table lacks required identity/date/filter fields; no assumptions made.")
        fields = [name for name in FIELDS if name in columns]
        table = f"{_identifier(schema)}.\"funda\""
        # Confirm read access separately from whether EOG has data.
        self._query(f"SELECT gvkey FROM {table} LIMIT 0")

        identity_table = table
        identity_columns = columns
        if "tic" not in columns:
            if "company" not in tables:
                raise WRDSError("No ticker field or company identity table is available.")
            identity_columns = set(self._columns(schema, "company")["name"])
            if not {"gvkey", "tic"}.issubset(identity_columns):
                raise WRDSError("Company table lacks ticker/GVKEY identity fields.")
            identity_table = f"{_identifier(schema)}.\"company\""
        identity_fields = [name for name in ("gvkey", "tic", "conm") if name in identity_columns]
        identities = self._query(
            f"SELECT DISTINCT {', '.join(_identifier(name) for name in identity_fields)} "
            f"FROM {identity_table} WHERE UPPER(tic) = %(ticker)s LIMIT 20",
            {"ticker": ticker},
        )
        if identities.empty:
            return {"status": "available_no_eog", "table": f"{schema}.funda", "records": []}
        if len(identities) >= 20:
            raise WRDSError("Ticker lookup reached its sample limit; identity needs review.")
        gvkeys = identities["gvkey"].dropna().unique()
        if len(gvkeys) != 1:
            raise WRDSError("Ticker matched multiple or missing GVKEYs; identity needs review.")
        params = {"gvkey": gvkeys[0], "limit": limit, **FILTERS}
        conditions = " AND ".join(f'{_identifier(name)} = %({name})s' for name in FILTERS)
        sql = (
            f"SELECT {', '.join(_identifier(name) for name in fields)} FROM {table} "
            f"WHERE gvkey = %(gvkey)s AND {conditions} "
            "ORDER BY datadate DESC, fyear DESC LIMIT %(limit)s"
        )
        frame = self._query(sql, params)
        if not frame.empty:
            if frame["gvkey"].isna().any() or not frame["gvkey"].eq(gvkeys[0]).all():
                raise WRDSError("Returned company identity does not match the selected GVKEY.")
            if frame.duplicated(subset=["gvkey", "datadate"]).any():
                raise WRDSError("Duplicate annual records require review; no rows were silently removed.")
        return {
            "status": "available" if not frame.empty else "available_no_eog",
            "table": f"{schema}.funda",
            "identity_records": _records(identities),
            "selected_fields": fields,
            "unavailable_fields": [name for name in FIELDS if name not in columns],
            "field_metadata": _records(description[description["name"].isin(fields)]),
            "filters": FILTERS,
            "query": sql,
            "parameters": params,
            "records": _records(frame),
            "units_note": "Provider units retained; inspect column comments and Compustat documentation before mapping. No scaling performed.",
        }

    def retrieve_eog(self, inventory: dict, limit: int = 5) -> dict:
        if type(limit) is not int or not 1 <= limit <= 10:
            raise WRDSError("Sample limit must be an integer from 1 to 10.")
        # Full North America candidates only. Sample/global libraries remain
        # listed in inventory, but cannot establish full NA access.
        candidates = [
            schema for schema in inventory["compustat_schema_candidates"]
            if not any(part in schema.lower() for part in ("samp", "global", "bank"))
            and "funda" in inventory["tables_by_library"].get(schema, [])
        ]
        priority = {"comp": 0, "comp_na_daily": 1, "compa": 2, "comp_na_annual": 3}
        candidates.sort(key=lambda name: (priority.get(name, 4), name))
        attempts = []
        available_empty = None
        for schema in candidates:
            try:
                result = self._annual_sample(schema, inventory["tables_by_library"][schema], "EOG", limit)
                attempts.append({"table": f"{schema}.funda", "status": result["status"]})
                if result["status"] == "available":
                    return {**result, "attempts": attempts}
                available_empty = result
            except WRDSAccessError:
                attempts.append({"table": f"{schema}.funda", "status": "access_denied"})
            except WRDSError as error:
                attempts.append({"table": f"{schema}.funda", "status": "undetermined", "detail": str(error)})
        if available_empty:
            return {**available_empty, "attempts": attempts}
        uncertain = any(item["status"] == "undetermined" for item in attempts) or any(
            schema in inventory["catalog_errors"] for schema in inventory["compustat_schema_candidates"]
        )
        return {
            "status": "undetermined" if uncertain else "not_available_in_account_catalogue",
            "table": None, "records": [], "attempts": attempts,
        }


def save_report(inventory: dict, sample: dict, root: Path = RAW_ROOT) -> Path:
    stamp = datetime.now(timezone.utc)
    directory = root / f"{stamp:%Y%m%dT%H%M%S%fZ}_{uuid4().hex[:8]}"
    report = {
        "provider": "WRDS", "retrieved_at_utc": stamp.isoformat(), "test_ticker": "EOG",
        "inventory": inventory, "compustat": sample,
        "serialization": "Provider field names unchanged; Decimal/date/type metadata serialized as strings, SQL missing values as null.",
    }
    try:
        directory.mkdir(parents=True, exist_ok=False)
        path = directory / "verification.json"
        path.write_text(json.dumps(report, indent=2, default=str, allow_nan=False), encoding="utf-8")
    except (OSError, ValueError):
        raise WRDSError("Could not save the local WRDS inspection report.") from None
    return path
