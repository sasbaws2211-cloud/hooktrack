"""Checks that the page a visitor came from is saved safely at signup. Run: python tests/signup_ref_check.py (local test database)."""
import os, sys, time
os.environ.setdefault("DATABASE_URL", "postgresql://hooktrack:ht_dev_pw@127.0.0.1:55434/hooktrack")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from app import auth, billing, db
from app.main import app

db.init_schema()
billing._pset(auth.SETTING_SIGNUP_IP_DAILY, 1000)
auth.DOMAIN_SIGNUPS_PER_HOUR = 1000          # this test makes many accounts from one domain on purpose
db.execute("DELETE FROM rate_limits")
sfx = str(int(time.time()))
ok = lambda n, cond: print(("PASS " if cond else "FAIL ") + n) or (cond or sys.exit(1))


def signup(tag, ref=None):
    c = TestClient(app)
    email = f"ref{tag}{sfx}@example.com"
    body = {"email": email, "password": "Passw0rd!long1", "name": "R", "workspace_name": "REF " + tag}
    if ref is not None:
        body["ref"] = ref
    r = c.post("/api/auth/signup", json=body)
    assert r.status_code == 200, r.text
    return db.one("SELECT signup_ref FROM users WHERE email=%s", (email,))["signup_ref"]


ok("a page tag is saved", signup("a", "tools-tiktok-analytics") == "tools-tiktok-analytics")
ok("upper case is normalised", signup("b", "Tools-Best-Time") == "tools-best-time")
ok("no tag means direct (empty)", signup("c") == "")
ok("script-looking input is dropped", signup("d", "<script>alert(1)</script>") == "")
ok("spaces, slashes and dots are dropped", signup("e", "a/b c.d") == "")
ok("an over-long tag is dropped", signup("f", "a" * 100) == "")
ok("clean_ref accepts only a safe slug", auth.clean_ref("compare") == "compare" and auth.clean_ref("-bad") == "" and auth.clean_ref("") == "" and auth.clean_ref(None) == "")

src = {s["source"]: s for s in auth.signup_sources()}
ok("sources count the tagged page", src["tools-tiktok-analytics"]["total"] >= 1 and src["tools-tiktok-analytics"]["last_30_days"] >= 1)
ok("untagged signups are shown as direct", "direct" in src and src["direct"]["total"] >= 3)
print("ALL OK")
