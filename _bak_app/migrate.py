"""Forward-only SQL migrations.

Files in app/migrations/ named NNNN_description.sql run once, in order, each in its own transaction.
Applied versions are recorded in schema_migrations with a checksum, so editing an applied file is caught.
A Postgres advisory lock serialises concurrent starts (several workers booting at once).

    python -m app.migrate status     # applied / pending
    python -m app.migrate up         # apply pending (the app does this at startup)

To change the schema: add the next numbered file. Never edit one that has shipped.
Rolling back = restoring a backup or writing a new forward migration.
"""
import hashlib
import logging
import re
import sys
from pathlib import Path

import psycopg2

from . import db

log = logging.getLogger("hooktrack.migrate")
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
LOCK_KEY = 727_001          # arbitrary app-wide advisory lock id
NAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationError(RuntimeError):
    pass


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def discover(directory: Path = MIGRATIONS_DIR) -> list[tuple[str, Path]]:
    found = []
    for p in sorted(Path(directory).glob("*.sql")):
        if not NAME_RE.match(p.name):
            raise MigrationError(f"Bad migration file name: {p.name} (expected 0001_description.sql)")
        found.append((p.name[:4], p))
    versions = [v for v, _ in found]
    if len(set(versions)) != len(versions):
        raise MigrationError("Two migration files share a version number")
    return found


def _applied(cur) -> dict:
    cur.execute("SELECT version, checksum FROM schema_migrations")
    return dict(cur.fetchall())


def status(url: str | None = None, directory: Path = MIGRATIONS_DIR) -> dict:
    c = psycopg2.connect(url or db.database_url())
    try:
        with c, c.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                        "version TEXT PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, "
                        "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
            done = _applied(cur)
        files = discover(directory)
        return {"applied": [v for v, _ in files if v in done],
                "pending": [p.name for v, p in files if v not in done]}
    finally:
        c.close()


def up(url: str | None = None, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every pending migration. Returns the file names applied."""
    files = discover(directory)
    c = psycopg2.connect(url or db.database_url())
    applied_now = []
    try:
        c.autocommit = True
        with c.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
            try:
                cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                            "version TEXT PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, "
                            "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
                done = _applied(cur)
                for version, path in files:
                    sql = path.read_text(encoding="utf-8")
                    digest = _checksum(sql)
                    if version in done:
                        if done[version] != digest:
                            raise MigrationError(
                                f"{path.name} was changed after it was applied. Add a new migration instead of editing it.")
                        continue
                    log.info("applying migration %s", path.name)
                    c.autocommit = False
                    try:
                        cur.execute(sql)
                        cur.execute("INSERT INTO schema_migrations(version, name, checksum) VALUES (%s,%s,%s)",
                                    (version, path.name, digest))
                        c.commit()
                    except Exception as e:
                        c.rollback()
                        raise MigrationError(f"{path.name} failed and was rolled back: {e}") from e
                    finally:
                        c.autocommit = True
                    applied_now.append(path.name)
            finally:
                cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
    finally:
        c.close()
    return applied_now


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        s = status()
        print("applied:", ", ".join(s["applied"]) or "-")
        print("pending:", ", ".join(s["pending"]) or "-")
    elif cmd == "up":
        done = up()
        print("applied:", ", ".join(done) or "nothing to do")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
