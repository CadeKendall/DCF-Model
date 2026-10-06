"""Verify built-in SQLite works without creating a database file."""

import sqlite3
from contextlib import closing


def test_sqlite_round_trip() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("CREATE TABLE environment_check (value TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO environment_check (value) VALUES (?)", ("ready",)
        )
        connection.commit()

        result = connection.execute("SELECT value FROM environment_check").fetchone()

    assert result == ("ready",)
