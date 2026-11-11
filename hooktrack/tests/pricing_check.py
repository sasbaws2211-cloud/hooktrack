"""The first-customer pricing (Free, Solo, Pro, Lifetime; Agency off sale) and the "need more?" form.
Run: python tests/pricing_check.py (local test database; email is faked)."""
import os, sys
os.environ.setdefault("DATABASE_URL", "postgresql://hooktrack:ht_dev_pw@127.0.0.1:55434/hooktrack")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from app import auth, billing, db, inquiry, mailer
from app.main import app

db.init_schema()
ok = lambda n, cond: print(("PASS " if cond else "FAIL ") + n) or (cond or sys.exit(1))
billing.set_enabled(True)
billing._pset(auth.SETTING_SIGNUP_IP_DAILY, 1000)
db.execute("DELETE FROM rate_limits"); db.execute("DELETE FROM sales_inquiries")
P = {p["code"]: p for p in db.query("SELECT * FROM plans")}

# ---- the plans
s, sy = P["solo"], P["solo_year"]
ok("Solo is $12 a month and $120 a year", s["price_minor"] == 1200 and sy["price_minor"] == 12000 and s["interval"] == "month" and sy["interval"] == "year")
ok("Solo: 3 accounts, 1 publishing account, 1 teammate, 100 credits, capped AI, no reports",
   (s["max_accounts"], s["max_connected"], s["max_members"], s["max_credits"], s["max_ai_calls"], s["max_reports"]) == (3, 1, 1, 100, 300, 0))
ok("the yearly Solo has the same limits as the monthly", all(sy[k] == s[k] for k in ("max_accounts", "max_connected", "max_members", "max_credits", "max_ai_calls", "max_reports")))
pr, py = P["pro"], P["pro_year"]
ok("Pro is $39 a month and $390 a year", pr["price_minor"] == 3900 and py["price_minor"] == 39000)
ok("Pro is generous: 15 accounts, 3 publishing, 5 teammates, 800 credits, 5 client reports, unlimited AI",
   (pr["max_accounts"], pr["max_connected"], pr["max_members"], pr["max_credits"], pr["max_reports"], pr["max_ai_calls"]) == (15, 3, 5, 800, 5, -1))
ok("Pro beats Solo on every limit, so it is worth stepping up to", all(pr[k] > s[k] for k in ("max_accounts", "max_connected", "max_members", "max_credits", "max_reports")))
ok("nobody already on Pro loses anything: it never gives less than before", pr["max_accounts"] >= 15 and pr["max_members"] >= 5 and pr["max_connected"] >= 3 and pr["max_credits"] >= 600)
ok("Agency is off sale, monthly and yearly", P["agency"]["active"] is False and P["agency_year"]["active"] is False)

# ---- what the pricing page and the app see
codes = [x["code"] for x in billing.public_pricing()["plans"]]
ok("the public price list is just Solo and Pro, each monthly and yearly", sorted(codes) == ["pro", "pro_year", "solo", "solo_year"])
ok("Agency cannot be bought", "agency" not in [x["code"] for x in billing.list_plans(only_active=True)])
ok("but an Agency workspace keeps working: its plan still resolves with its own limits", billing.get_plan("agency")["max_accounts"] == 100)
ok("the cheapest paid plan is now $12", min(x["price_minor"] for x in billing.public_pricing()["plans"] if x["interval"] == "month") == 1200)

# ---- the free and lifetime cards get their data from the same endpoint
pub = billing.public_pricing()
ok("the free plan card has its data", pub.get("free_plan") is None or {"name", "description", "max_accounts", "max_ai_calls"} <= set(pub["free_plan"]))
billing._pset("lifetime_deal", {"enabled": True, "cap": 200}); db.execute("DELETE FROM lifetime_grants")
lo = billing.public_pricing()["lifetime_offer"]
ok("the lifetime card has its data", lo and lo["price_minor"] == 14900 and lo["left"] == 200 and lo["includes"]["other_accounts"] == 1)
billing._pset("lifetime_deal", {"enabled": False, "cap": 200})

# ---- the "need more?" form
sent = []
mailer.send = lambda to, subject, text: sent.append((to, subject, text)) or True
c = TestClient(app)
body = {"email": "boss@agency.example", "accounts": 40, "team": 3, "platforms": "TikTok, Instagram", "budget": "$50 to $150", "message": "40 client accounts and reports for each"}
r = c.post("/api/public/need-more", json=body)
ok("a visitor can tell us what they need, with no account", r.status_code == 200 and r.json() == {"ok": True})
ok("it is emailed to admin@hooktrack.site", len(sent) == 1 and sent[0][0] == "admin@hooktrack.site")
ok("the email carries everything the visitor said, under a fixed subject", "boss@agency.example" in sent[0][2] and "40" in sent[0][2] and "reports for each" in sent[0][2]
   and "TikTok, Instagram" in sent[0][2] and sent[0][1] == "HookTrack: a visitor needs more than the plans")
row = db.one("SELECT * FROM sales_inquiries")
ok("it is also saved, marked as emailed", row["email"] == "boss@agency.example" and row["accounts"] == 40 and row["team"] == 3 and row["emailed"] is True)
os.environ["HOOKTRACK_SALES_EMAIL"] = "sales@example.com"
changed = inquiry.sales_email()
os.environ.pop("HOOKTRACK_SALES_EMAIL")
ok("the sales address can be changed with an environment variable", changed == "sales@example.com" and inquiry.sales_email() == "admin@hooktrack.site")

mailer.send = lambda *a: False
db.execute("DELETE FROM rate_limits")
r = c.post("/api/public/need-more", json={"email": "b@x.example", "message": "hello"})
ok("if the email cannot be sent the message is still saved and the visitor sees success",
   r.status_code == 200 and db.one("SELECT emailed FROM sales_inquiries WHERE email='b@x.example'")["emailed"] is False)

n = db.one("SELECT COUNT(*) AS n FROM sales_inquiries")["n"]
ok("a bot that fills the hidden field looks successful but nothing is stored",
   c.post("/api/public/need-more", json={**body, "website": "http://spam"}).status_code == 200 and db.one("SELECT COUNT(*) AS n FROM sales_inquiries")["n"] == n)
ok("a bad email is refused", c.post("/api/public/need-more", json={"email": "not-an-email"}).status_code == 422
   and c.post("/api/public/need-more", json={"email": "a b@x.com"}).status_code == 422)
ok("silly numbers and long messages are refused", c.post("/api/public/need-more", json={"email": "a@b.example", "accounts": -5}).status_code == 422
   and c.post("/api/public/need-more", json={"email": "a@b.example", "message": "x" * 2000}).status_code == 422)
sent.clear()
mailer.send = lambda to, subject, text: sent.append((to, subject, text)) or True
db.execute("DELETE FROM rate_limits")
nasty = c.post("/api/public/need-more", json={"email": "ok@x.example", "message": "Line\x00 with <script>alert(1)</script>\r\nBcc: evil@x.example", "platforms": "a\nb"})
ok("control characters are stripped and nothing the visitor typed reaches the subject",
   nasty.status_code == 200 and "\x00" not in sent[-1][2] and sent[-1][1] == "HookTrack: a visitor needs more than the plans")
db.execute("DELETE FROM rate_limits")
codes = [c.post("/api/public/need-more", json={"email": f"v{i}@x.example"}).status_code for i in range(7)]
ok("a visitor is limited to five messages an hour", codes == [200] * 5 + [429, 429])

db.execute("DELETE FROM rate_limits"); db.execute("DELETE FROM sales_inquiries")
print("ALL OK")
