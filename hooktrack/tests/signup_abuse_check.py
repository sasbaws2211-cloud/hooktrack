"""Sign-up cannot be used to make this server send mail to addresses that cannot receive it.
Run: python tests/signup_abuse_check.py (local test database; no email is sent and DNS is faked, except two optional live lookups)."""
import os, sys
os.environ.setdefault("DATABASE_URL", "postgresql://hooktrack:ht_dev_pw@127.0.0.1:55434/hooktrack")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from app import auth, billing, db, emailguard, mailer
from app.main import app

db.init_schema()
ok = lambda n, cond: print(("PASS " if cond else "FAIL ") + n) or (cond or sys.exit(1))
billing._pset(auth.SETTING_SIGNUP_IP_DAILY, 1000)
billing._pset(auth.SETTING_SIGNUP_GLOBAL_HOURLY, 150)
c = TestClient(app)


def reset():
    db.execute("DELETE FROM rate_limits")
    emailguard._dns_cache.clear()


def clean():
    db.execute("DELETE FROM users WHERE email LIKE 'abuse.%%'")


def go(email, **extra):
    return c.post("/api/auth/signup", json={"name": "T", "workspace_name": "W", "email": email, "password": "Testpass123!", **extra})


reset(); clean()

# ---- when this server cannot send email, nothing is checked (development, the demo and the tests keep working)
ok("with no email configured, a made-up address still signs up", go("abuse.dev@studio-sample.example").status_code == 200)
clean(); reset()

# ---- when it can send email, addresses that cannot receive mail are refused before anything is sent
mailer.configured = lambda: True
lookups = []
emailguard._lookup = lambda d: lookups.append(d) or {"realmail-domain.io": True, "nomail.org": False}.get(d, None)
ok("a reserved test domain (.example) is refused", go("abuse.a@studio-sample.example").status_code == 400)
ok("so are .test, .invalid, .localhost and example.com", all(go(f"abuse.b@{d}").status_code == 400 for d in ("x.test", "x.invalid", "x.localhost", "example.com", "mail.example.org")))
r = go("abuse.c@nomail.org")
ok("a domain with no mail records is refused, with a plain message", r.status_code == 400 and "can't receive mail" in r.json()["detail"])
ok("and no account was created for any of them", db.one("SELECT COUNT(*) AS n FROM users WHERE email LIKE 'abuse.%%'")["n"] == 0)
ok("a domain whose DNS could not be checked is allowed (a DNS hiccup never blocks a real person)", go("abuse.d@unknown-dns.com").status_code == 200)
clean(); reset()
ok("a real mail domain is allowed", go("abuse.e@realmail-domain.io").status_code == 200)
clean(); reset()

# ---- the hidden trap field
n = db.one("SELECT COUNT(*) AS n FROM users")["n"]
ok("a bot that fills the hidden field is refused and nothing is created", go("abuse.f@realmail-domain.io", website="http://spam").status_code == 400
   and db.one("SELECT COUNT(*) AS n FROM users")["n"] == n)

# ---- one email domain cannot create account after account
reset(); clean()
codes = []
for i in range(7):
    db.execute("DELETE FROM rate_limits WHERE key LIKE 'signup:%%'")          # the per-address hourly limit is a different brake
    codes.append(go(f"abuse.g{i}@botfarm-corp.io").status_code)
ok("a single email domain is limited to five new accounts an hour", codes == [200] * 5 + [429, 429])
reset(); clean()
codes = []
for i in range(7):
    db.execute("DELETE FROM rate_limits WHERE key LIKE 'signup:%%'")
    codes.append(go(f"abuse.h{i}@gmail.com").status_code)
ok("the big mailbox providers are exempt from that limit", codes == [200] * 7)

# ---- a site-wide hourly ceiling
reset(); clean()
billing._pset(auth.SETTING_SIGNUP_GLOBAL_HOURLY, 3)
codes = []
for i in range(5):
    db.execute("DELETE FROM rate_limits WHERE key LIKE 'signup:%%'")
    codes.append(go(f"abuse.i{i}@gmail.com").status_code)
ok("only so many accounts are created in an hour, whoever they come from", codes == [200, 200, 200, 429, 429])
r = go("abuse.j@gmail.com")
ok("and the message tells a real person to try again shortly", r.status_code == 429 and "try again" in r.json()["detail"])
billing._pset(auth.SETTING_SIGNUP_GLOBAL_HOURLY, 150)

# ---- the helper itself
ok("reserved-domain detection", all(emailguard.is_reserved(d) for d in ("a.example", "x.test", "foo.invalid", "example.com", "mail.example.net", "x.localhost"))
   and not any(emailguard.is_reserved(d) for d in ("gmail.com", "example.co", "myexample.com", "test.io")))

reset(); clean()
print("ALL OK")
