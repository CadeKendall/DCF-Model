"""Refresh annual inputs interactively; credentials stay outside the notebook."""

import argparse

from app.wrds_client import WRDSClient, WRDSError, save_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", default="EOG")
    parser.add_argument("--years", type=int, default=6,
                        help="Retrieve one extra year for changes; maximum 10.")
    args = parser.parse_args()
    ticker = args.ticker.strip().upper()
    client = WRDSClient()
    try:
        client.connect()
        inventory = client.inspect_account()
        result = client.retrieve_annual(inventory, ticker=ticker, limit=args.years)
        path = save_report(inventory, result, ticker=ticker)
        print(f"Status: {result['status']}; records: {len(result['records'])}")
        print(f"Saved: {path}")
        return 0 if result["status"] == "available" else 1
    except (WRDSError, EOFError, KeyboardInterrupt) as error:
        print(str(error) if isinstance(error, WRDSError) else "Refresh cancelled.")
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
