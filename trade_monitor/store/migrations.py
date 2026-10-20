"""Schema versioning + migrations (STORE-1).

Each SQLite store records its schema version in the database's `user_version`
pragma. On open, the store migrates forward to the current version by applying
any registered steps it has not yet seen.

Rules:

  * a migration is a list of SQL statements that take the schema from version N
    to N+1, so they must be applied in order
  * a database at a *newer* version than this code knows is refused, not
    silently downgraded -- that would lose data written by a newer release
  * `user_version = 0` means "unversioned, from before this module existed";
    the current schema is applied with `CREATE TABLE IF NOT EXISTS`, which is
    idempotent, so an existing DB is adopted without data loss
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence


class SchemaTooNewError(RuntimeError):
    """The database was written by a newer version of the code."""


# Version 1 is the current baseline. Future changes append to MIGRATIONS.
CURRENT_VERSION = 1


def _applied(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    # PRAGMA does not accept bound parameters; version is an int we control.
    conn.execute(f"PRAGMA user_version = {int(version)}")


# migrations[n] takes the schema from version n to n+1.
MIGRATIONS: dict[int, Sequence[str]] = {
    # No steps yet. When a column is added, add it here as version 1 -> 2.
}


def migrate(
    conn: sqlite3.Connection,
    *,
    current: int = CURRENT_VERSION,
    migrations: dict[int, Sequence[str]] | None = None,
) -> int:
    """Bring `conn` up to `current`. Returns the version it ends at.

    Never downgrades. Raises SchemaTooNewError if the DB is ahead of the code.
    """
    migrations = migrations if migrations is not None else MIGRATIONS
    have = _applied(conn)

    if have > current:
        raise SchemaTooNewError(
            f"database schema is version {have}, this build only knows {current}. "
            f"Upgrade trade-monitor; refusing to open and risk data loss."
        )
    if have == current:
        return have

    for version in range(have, current):
        for stmt in migrations.get(version, ()):
            conn.execute(stmt)
        _set_version(conn, version + 1)

    if have == 0 and current >= 1 and not migrations:
        # Adopt an unversioned DB (which already ran CREATE TABLE IF NOT EXISTS)
        # at the baseline version without touching its data.
        _set_version(conn, current)

    conn.commit()
    return _applied(conn)


def schema_version(conn: sqlite3.Connection) -> int:
    return _applied(conn)


__all__ = [
    "migrate", "schema_version", "SchemaTooNewError",
    "CURRENT_VERSION", "MIGRATIONS",
]
