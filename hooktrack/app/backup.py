"""Database backups you can prove work.

    python -m app.backup create                         # write a backup now
    python -m app.backup list
    python -m app.backup drill [archive]                # restore the newest (or given) backup into a scratch
                                                        # database, compare it with the original, drop it
    python -m app.backup restore <archive> <target-url> # into an EMPTY database you name; never the live one by default

A backup is a gzip'd tar of one text-format COPY file per table plus a manifest (row count + order-independent
checksum per table, sequence values, migration versions). It is taken in one REPEATABLE READ snapshot so the tables
agree with each other, and needs nothing but the database driver the app already uses (no pg_dump).

IMPORTANT: it holds customers' data and password hashes, and encrypted API keys that can only be read with
HOOKTRACK_SECRET. Keep the archives private and keep HOOKTRACK_SECRET somewhere separate and safe: a backup without it
restores, but every stored key has to be re-entered.

Settings (environment): HOOKTRACK_BACKUP=0 turns the schedule off; HOOKTRACK_BACKUP_DIR (default ./backups);
HOOKTRACK_BACKUP_KEEP (default 14 archives); HOOKTRACK_BACKUP_HOURS (default 24);
HOOKTRACK_BACKUP_DRILL_DAYS (default 7); HOOKTRACK_BACKUP_COPY_TO = a second directory (a synced or mounted
folder, i.e. off this machine) each new archive is also copied to.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2

from . import crypto, db, leases, migrate

log = logging.getLogger("hooktrack.backup")

STATUS_KEY = "backup_status"
LEASE = "database-backup"
SKIP = {"rate_limits", "job_leases"}          # transient: nothing worth restoring
FORMAT = 1


class BackupError(RuntimeError):
    pass


# ---------- settings ----------

def backup_dir() -> Path:
    return Path(os.environ.get("HOOKTRACK_BACKUP_DIR") or Path(__file__).parent.parent / "backups")


def enabled() -> bool:
    return (os.environ.get("HOOKTRACK_BACKUP", "1").strip().lower() not in ("0", "false", "off", "no"))


def _num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def keep() -> int:
    return max(2, int(_num("HOOKTRACK_BACKUP_KEEP", 14)))


# ---------- helpers ----------

def _secret_fingerprint() -> str:
    return hashlib.sha256(crypto._load_secret().encode()).hexdigest()[:12]


def _tables(cur) -> list[str]:
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
    return [r[0] for r in cur.fetchall() if r[0] not in SKIP]


def _columns(cur, table: str) -> list[str]:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s "
                "ORDER BY ordinal_position", (table,))
    return [r[0] for r in cur.fetchall()]


def _fk_order(cur, tables: list[str]) -> tuple[list[str], bool]:
    """Parents before children. Returns (order, had_cycle). A table referencing itself is not a cycle."""
    cur.execute("""SELECT c.conrelid::regclass::text, c.confrelid::regclass::text FROM pg_constraint c
                   JOIN pg_namespace n ON n.oid = c.connamespace WHERE c.contype='f' AND n.nspname='public'""")
    deps = {t: set() for t in tables}
    for child, parent in cur.fetchall():
        child, parent = child.strip('"'), parent.strip('"')
        if child in deps and parent in deps and child != parent:
            deps[child].add(parent)
    order, left = [], dict(deps)
    while left:
        ready = sorted(t for t, d in left.items() if not (d & set(left)))
        if not ready:
            return order + sorted(left), True
        order += ready
        for t in ready:
            del left[t]
    return order, False


def _digest(cur, table: str, cols: list[str]) -> dict:
    """Row count and an order-independent checksum of the table's rows. Columns are named explicitly, in the order
    the manifest records, because a database upgraded in place can hold the same columns in another physical order."""
    row = "ROW(" + ",".join('t."' + c + '"' for c in cols) + ")::text"
    cur.execute(f"SELECT count(*), COALESCE(SUM(('x'||substr(md5({row}),1,15))::bit(60)::bigint),0)::text "
                f'FROM "{table}" t')
    n, total = cur.fetchone()
    return {"rows": n, "checksum": total}


def _utc_session(cur) -> None:
    cur.execute("SET TIME ZONE 'UTC'")


def _record(**kw) -> dict:
    cur = db.one("SELECT value FROM platform_settings WHERE key=%s", (STATUS_KEY,))
    s = dict((cur or {}).get("value") or {}) if cur and isinstance(cur.get("value"), dict) else {}
    s.update(kw)
    db.execute("""INSERT INTO platform_settings(key, value, updated_at) VALUES (%s,%s,now())
                  ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""", (STATUS_KEY, db.Json(s)))
    return s


def status() -> dict:
    row = db.one("SELECT value FROM platform_settings WHERE key=%s", (STATUS_KEY,))
    s = row["value"] if row and isinstance(row.get("value"), dict) else {}
    return {"enabled": enabled(), "dir": str(backup_dir()), "keep": keep(),
            "copy_to": os.environ.get("HOOKTRACK_BACKUP_COPY_TO", "").strip(),
            "last_backup_at": s.get("last_backup_at"), "last_backup_file": s.get("last_backup_file"),
            "last_backup_bytes": s.get("last_backup_bytes"), "last_backup_rows": s.get("last_backup_rows"),
            "last_error": s.get("last_error") or "", "last_drill_at": s.get("last_drill_at"),
            "drill_ok": s.get("drill_ok"), "drill_seconds": s.get("drill_seconds"),
            "drill_report": s.get("drill_report"), "drill_error": s.get("drill_error") or "",
            "files": list_archives()[:10]}


def list_archives(directory: Path | None = None) -> list[dict]:
    d = directory or backup_dir()
    if not d.exists():
        return []
    out = [{"name": p.name, "bytes": p.stat().st_size,
            "at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()}
           for p in d.glob("hooktrack-*.tar.gz")]
    return sorted(out, key=lambda x: x["name"], reverse=True)


# ---------- create ----------

def create(directory: Path | None = None) -> dict:
    d = directory or backup_dir()
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    final = d / f"hooktrack-{stamp}.tar.gz"
    work = Path(tempfile.mkdtemp(prefix="htbackup-"))
    conn = psycopg2.connect(db.database_url())
    try:
        conn.set_session(isolation_level="REPEATABLE READ", readonly=True)
        cur = conn.cursor()
        _utc_session(cur)
        tables = _tables(cur)
        manifest = {"format": FORMAT, "created_at": datetime.now(timezone.utc).isoformat(), "tables": {},
                    "secret_fingerprint": _secret_fingerprint(), "sequences": {}}
        cur.execute("SELECT version FROM schema_migrations ORDER BY version")
        manifest["migrations"] = [r[0] for r in cur.fetchall()]
        for t in tables:
            cols = _columns(cur, t)
            path = work / f"{t}.copy"
            with open(path, "wb") as f:
                cur.copy_expert(f'COPY "{t}" ({",".join(chr(34) + c + chr(34) for c in cols)}) TO STDOUT', f)
            manifest["tables"][t] = {"columns": cols, **_digest(cur, t, cols)}
        cur.execute("SELECT sequencename, last_value FROM pg_sequences WHERE schemaname='public'")
        manifest["sequences"] = {n: v for n, v in cur.fetchall() if v is not None}
        (work / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        tmp = final.with_suffix(".part")
        with tarfile.open(tmp, "w:gz") as tar:
            tar.add(work / "manifest.json", arcname="manifest.json")
            for t in tables:
                tar.add(work / f"{t}.copy", arcname=f"tables/{t}.copy")
        os.replace(tmp, final)
        try:
            os.chmod(final, 0o600)
        except OSError:
            pass
    finally:
        conn.close()
        shutil.rmtree(work, ignore_errors=True)
    rows = sum(t["rows"] for t in manifest["tables"].values())
    out = {"file": final.name, "path": str(final), "bytes": final.stat().st_size, "rows": rows,
           "tables": len(manifest["tables"])}
    copy_to = os.environ.get("HOOKTRACK_BACKUP_COPY_TO", "").strip()
    if copy_to:
        Path(copy_to).mkdir(parents=True, exist_ok=True)
        shutil.copy2(final, Path(copy_to) / final.name)
        out["copied_to"] = copy_to
    for old in list_archives(d)[keep():]:
        try:
            (d / old["name"]).unlink()
        except OSError:
            pass
    return out


# ---------- restore ----------

def _read_manifest(archive: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        f = tar.extractfile("manifest.json")
        if f is None:
            raise BackupError("not a HookTrack backup (no manifest)")
        return json.loads(f.read().decode("utf-8"))


def _same_db(a: str, b: str) -> bool:
    ua, ub = urlsplit(a), urlsplit(b)
    return (ua.hostname, ua.port or 5432, ua.path) == (ub.hostname, ub.port or 5432, ub.path)


def restore(archive: str | Path, target_url: str, allow_live: bool = False) -> dict:
    """Load `archive` into the database at `target_url` (schema built by the app's own migrations), replacing
    whatever it holds, then check every table against the manifest. Raises BackupError on any mismatch."""
    archive = Path(archive)
    if _same_db(target_url, db.database_url()) and not allow_live:
        raise BackupError("Refusing to restore over the live database. Restore into a scratch database, or pass "
                          "--allow-live if you really mean to replace production.")
    manifest = _read_manifest(archive)
    if manifest.get("format") != FORMAT:
        raise BackupError(f"unsupported backup format {manifest.get('format')}")
    known = {v for v, _ in migrate.discover()}
    unknown = [v for v in manifest.get("migrations", []) if v not in known]
    if unknown:
        raise BackupError(f"this backup comes from a newer version of HookTrack (migrations {', '.join(unknown)}); "
                          f"update the app before restoring it")
    migrate.up(target_url)
    started = time.time()
    conn = psycopg2.connect(target_url)
    problems: list[str] = []
    try:
        cur = conn.cursor()
        _utc_session(cur)
        present = set(_tables(cur))
        tables = [t for t in manifest["tables"] if t in present]
        missing = [t for t in manifest["tables"] if t not in present]
        if missing:
            raise BackupError(f"tables in the backup that this version does not have: {', '.join(missing)}")
        order, cyclic = _fk_order(cur, tables)
        if cyclic:
            try:
                cur.execute("SET session_replication_role = replica")
            except psycopg2.Error:
                raise BackupError("tables reference each other in a cycle and this database user cannot relax "
                                  "constraints (needs superuser)")
        cur.execute("TRUNCATE " + ", ".join(f'"{t}"' for t in sorted(present)) + " RESTART IDENTITY CASCADE")
        # the application's own triggers (e.g. one that stamps filmed_at) must not rewrite rows as they load: a
        # restore has to put back exactly what was backed up. Foreign-key checks are system triggers and stay on.
        for t in order:
            cur.execute(f'ALTER TABLE "{t}" DISABLE TRIGGER USER')
        with tarfile.open(archive, "r:gz") as tar:
            for t in order:
                f = tar.extractfile(f"tables/{t}.copy")
                if f is None:
                    raise BackupError(f"backup is missing the data file for {t}")
                cols = manifest["tables"][t]["columns"]
                cur.copy_expert(f'COPY "{t}" ({",".join(chr(34) + c + chr(34) for c in cols)}) FROM STDIN', f)
        for t in order:
            cur.execute(f'ALTER TABLE "{t}" ENABLE TRIGGER USER')
        for seq, value in manifest.get("sequences", {}).items():
            cur.execute("SELECT setval(%s, %s, true)", (f'public."{seq}"', value))
        conn.commit()
        cur = conn.cursor()
        _utc_session(cur)
        checked = {}
        for t in order:
            want = manifest["tables"][t]
            got = _digest(cur, t, want["columns"])
            checked[t] = got["rows"]
            if got["rows"] != want["rows"]:
                problems.append(f"{t}: {got['rows']} rows restored, {want['rows']} expected")
            elif got["checksum"] != want["checksum"]:
                problems.append(f"{t}: contents differ from the backup")
        cur.execute("SELECT count(*) FROM users")
        users = cur.fetchone()[0]
    finally:
        conn.close()
    if problems:
        raise BackupError("restore did not match the backup: " + "; ".join(problems[:5]))
    return {"tables": len(checked), "rows": sum(checked.values()), "users": users,
            "seconds": round(time.time() - started, 1), "created_at": manifest["created_at"],
            "secret_matches": manifest.get("secret_fingerprint") == _secret_fingerprint()}


# ---------- the drill ----------

def _scratch_urls() -> tuple[str, str, str]:
    """(admin url on the 'postgres' database, scratch database name, scratch url) on the same server."""
    u = urlsplit(db.database_url())
    name = f"hooktrack_drill_{int(time.time())}"
    return urlunsplit(u._replace(path="/postgres")), name, urlunsplit(u._replace(path="/" + name))


def drill(archive: str | Path | None = None) -> dict:
    """Prove the newest backup restores: load it into a throwaway database, compare it table by table with what
    the backup says it holds, drop the database. Records the outcome for the admin console."""
    if archive is None:
        files = list_archives()
        if not files:
            raise BackupError("there is no backup to test yet")
        archive = backup_dir() / files[0]["name"]
    admin_url, name, scratch_url = _scratch_urls()
    t0 = time.time()
    c = psycopg2.connect(admin_url)
    c.autocommit = True
    try:
        try:
            c.cursor().execute(f'CREATE DATABASE "{name}"')
        except psycopg2.Error as e:
            raise BackupError(f"cannot create a scratch database for the drill (needs CREATEDB): {e}"[:300])
        try:
            report = restore(archive, scratch_url)
        finally:
            c.cursor().execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        c.close()
    report["archive"] = Path(archive).name
    report["seconds"] = round(time.time() - t0, 1)
    return report


# ---------- scheduled ----------

def _age(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return None


def _alert(kind: str, subject: str, detail: str) -> None:
    try:
        from . import vaultwatch
        vaultwatch._maybe_alert(kind, detail, subject=subject)
    except Exception:
        log.exception("could not send backup alert")


def backup_now() -> dict:
    """Create a backup and record it. Failures are recorded and alerted, then re-raised."""
    try:
        out = create()
    except Exception as e:
        _record(last_error=f"{type(e).__name__}: {e}"[:300])
        _alert("backup_failed", "HookTrack database backup FAILED", f"The scheduled backup failed: {e}")
        raise
    _record(last_backup_at=datetime.now(timezone.utc).isoformat(), last_backup_file=out["file"],
            last_backup_bytes=out["bytes"], last_backup_rows=out["rows"], last_error="")
    return out


def drill_now() -> dict:
    try:
        rep = drill()
    except Exception as e:
        _record(last_drill_at=datetime.now(timezone.utc).isoformat(), drill_ok=False, drill_error=str(e)[:300])
        _alert("drill_failed", "HookTrack backup restore test FAILED",
               f"The newest backup could not be restored and verified: {e}")
        raise
    _record(last_drill_at=datetime.now(timezone.utc).isoformat(), drill_ok=True, drill_seconds=rep["seconds"],
            drill_report={k: rep[k] for k in ("archive", "tables", "rows", "users", "secret_matches")}, drill_error="")
    return rep


def run_due() -> dict:
    """Called every ~30 minutes by every worker; the lease and the stored timestamps make it once per interval."""
    if not enabled():
        return {"enabled": False}
    out = {"enabled": True, "backup": None, "drill": None}
    with leases.lease(LEASE, ttl=1800) as got:
        if not got:
            return out
        s = status()
        age = _age(s["last_backup_at"])
        if age is None or age >= _num("HOOKTRACK_BACKUP_HOURS", 24) * 3600:
            try:
                out["backup"] = backup_now()
            except Exception:
                log.exception("scheduled backup failed")
                return out
        drill_age = _age(status()["last_drill_at"])
        if list_archives() and (drill_age is None or drill_age >= _num("HOOKTRACK_BACKUP_DRILL_DAYS", 7) * 86400):
            try:
                out["drill"] = drill_now()
            except Exception:
                log.exception("scheduled restore drill failed")
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else ""
    if cmd == "create":
        print(json.dumps(backup_now(), indent=1))
    elif cmd == "list":
        for f in list_archives():
            print(f"{f['name']}  {f['bytes']:>12,} bytes  {f['at']}")
    elif cmd == "drill":
        print(json.dumps(drill_now() if len(argv) < 2 else drill(argv[1]), indent=1))
    elif cmd == "restore" and len(argv) >= 3:
        print(json.dumps(restore(argv[1], argv[2], allow_live="--allow-live" in argv), indent=1))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
