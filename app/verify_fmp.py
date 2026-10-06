"""Run the five live EOG checks: python -m app.verify_fmp."""

import json

from app.fmp import FMPClient, FMPError, FMPInvalidTickerError


def main() -> int:
    try:
        client = FMPClient()
    except FMPError as error:
        print(f"FAIL: {error}")
        return 1

    checks = [
        ("Profile", client.get_profile, ("symbol", "companyName", "currency", "industry")),
        ("Quote", client.get_quote, ("symbol", "price", "timestamp")),
        ("Annual income", client.get_income_statement,
         ("symbol", "date", "period", "reportedCurrency", "revenue")),
        ("Annual balance sheet", client.get_balance_sheet,
         ("symbol", "date", "period", "reportedCurrency", "totalAssets")),
        ("Annual cash flow", client.get_cash_flow,
         ("symbol", "date", "period", "reportedCurrency", "operatingCashFlow")),
    ]
    failures = 0
    for label, retrieve, fields in checks:
        try:
            response = retrieve("EOG")
        except FMPError as error:
            failures += 1
            print(f"FAIL {label}: {error}")
            continue
        first = response.records[0]
        # Display only a few unchanged provider fields. Null/missing are explicit.
        sample = {name: first.get(name, "unavailable (field absent)") for name in fields}
        print(f"PASS {label}: {len(response.records)} record(s)")
        print(f"  First returned record: {json.dumps(sample, ensure_ascii=True)}")
        print(f"  Raw response: {response.raw_path}")

    live_passed = 5 - failures
    try:
        client.get_profile("INVALID TICKER!")
    except FMPInvalidTickerError:
        print("PASS invalid ticker: rejected locally before any HTTP request")
    else:
        failures += 1
        print("FAIL invalid ticker: expected rejection")

    print(f"Live EOG checks: {live_passed}/5 passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
