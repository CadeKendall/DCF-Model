"""Check that commands use the project's isolated Python environment."""

import sqlite3
import sys


def main() -> None:
    if sys.prefix == sys.base_prefix:
        raise SystemExit(
            "Use the project environment: "
            r".\.venv\Scripts\python.exe -m app.sanity"
        )

    print(f"Python: {sys.version.split()[0]}")
    print(f"Interpreter: {sys.executable}")
    print(f"SQLite: {sqlite3.sqlite_version}")
    print("Sanity check passed: a virtual environment is active.")


if __name__ == "__main__":
    main()
