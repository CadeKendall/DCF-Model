"""Interactive WRDS access check. Run locally; never paste credentials in chat."""

import json

from app.wrds_client import WRDSClient, WRDSError, save_report


def main() -> int:
    client = WRDSClient()
    print("Sign in through the standard WRDS prompts and approve the Duo Mobile push.")
    print("If offered a .pgpass file, enter n to avoid saving your password.")
    try:
        client.connect()
        print("PASS: WRDS authentication")
        inventory = client.inspect_account()
        print(f"PASS: {len(inventory['available_libraries'])} libraries listed")
        sample = client.retrieve_eog(inventory)
        path = save_report(inventory, sample)
        print("Relevant libraries:", ", ".join(inventory["relevant_libraries"]) or "none found")
        print("SEC-related schema candidates:", ", ".join(inventory["sec_related_schema_candidates"]) or "none found")
        print("Compustat status:", sample["status"])
        print("Table:", sample["table"] or "none selected")
        print("EOG records:", len(sample["records"]))
        for row in sample["records"][:2]:
            print("Raw field sample:", json.dumps(row, default=str, allow_nan=False))
        print("Saved inspection report:", path)
        if sample["status"] == "undetermined" or inventory["catalog_errors"]:
            print("Some access checks remain unresolved; inspect the saved report.")
            return 1
        return 0
    except WRDSError as error:
        print(f"INCOMPLETE: {error}")
        return 1
    except (KeyboardInterrupt, EOFError):
        print("WRDS verification cancelled; no credential details displayed.")
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
