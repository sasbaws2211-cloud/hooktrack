"""Sign-up and invitations accept only addresses in a standard format.
Run: python tests/email_format_check.py (local test database)."""
import os, sys
os.environ.setdefault("DATABASE_URL", "postgresql://hooktrack:ht_dev_pw@127.0.0.1:55434/hooktrack")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from app import auth, billing, db
from app.main import app

db.init_schema()
ok = lambda n, cond: print(("PASS " if cond else "FAIL ") + n) or (cond or sys.exit(1))
billing._pset(auth.SETTING_SIGNUP_IP_DAILY, 1000)
db.execute("DELETE FROM rate_limits")

GOOD = ["a@b.co", "user+tag@x.com", "first.last@sub.example.co.uk", "o'brien@x.com", "a_b-c@x-y.com", "x@xn--80ak6aa92e.com", "1@2.io"]
BAD = ["a@b", "a@b.c", "a@@b.com", "a b@x.com", "a@b..com", "a@.b.com", "a@b.com.", "a@-b.com", "a@b-.com", "a@b_c.com", ".a@b.com", "a.@b.com",
       "a..b@x.com", "a@b.123", "a@localhost", "a@1.2.3.4", "ñ@x.com", "a@x.com;b@y.com", "<a>@x.com", "a@x.c0m", "", "@x.com", "a@",
       "a@x.com\n", "x" * 65 + "@x.com", "a@" + "x" * 64 + ".com", "a@x.com,b@y.com", "plainaddress", "(a)@x.com"]
ok("standard addresses pass the format check", all(auth.valid_email_format(e) for e in GOOD))
ok("every malformed address fails it", not any(auth.valid_email_format(e) for e in BAD))

c = TestClient(app)
body = lambda e: {"name": "T", "workspace_name": "W", "email": e, "password": "Testpass123!"}
codes = {e: c.post("/api/auth/signup", json=body(e)).status_code for e in ["a@b..com", "a@b.com.", "a@b.123", "a@x.com ,b@y.com", "no-at-sign", "a@1.2.3.4"]}
ok("sign-up refuses bad addresses with a 400 and a helpful message", set(codes.values()) == {400}
   and "valid email" in c.post("/api/auth/signup", json=body("a@b..com")).json()["detail"])
ok("and nothing was created", db.one("SELECT COUNT(*) AS n FROM users WHERE email LIKE '%%b..com' OR email LIKE '%%b.123'")["n"] == 0)
e = "fmt.check@example.org"
db.execute("DELETE FROM users WHERE email=%s", (e,))
ok("a good address still signs up", c.post("/api/auth/signup", json=body("  Fmt.Check@Example.org ")).status_code == 200
   and db.one("SELECT 1 FROM users WHERE email=%s", (e,)) is not None)
db.execute("DELETE FROM rate_limits")
ok("sign-in keeps the loose check, so an older odd address can still log in", auth.EMAIL_RE.match("old..odd@x.com") is not None)
print("ALL OK")
