"""The lifetime deal: off until switched on, the cap, one purchase per workspace, what it includes, the account rule, add-ons,
refunds and the admin controls. Run: python tests/lifetime_check.py (local test database; Paystack is faked)."""
import os, sys, time
os.environ.setdefault("DATABASE_URL", "postgresql://hooktrack:ht_dev_pw@127.0.0.1:55434/hooktrack")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from app import auth, billing, db
from app.main import app
import admin.main as adm

db.init_schema()
ok = lambda n, cond: print(("PASS " if cond else "FAIL ") + n) or (cond or sys.exit(1))
billing._pset(auth.SETTING_SIGNUP_IP_DAILY, 1000)
db.execute("DELETE FROM rate_limits")
billing.set_enabled(True)
billing._pset("lifetime_deal", {"enabled": False, "cap": 200})
db.execute("DELETE FROM lifetime_grants")
db.execute("DELETE FROM payments WHERE plan_code = 'lifetime'")
db.execute("UPDATE plans SET price_minor = 14900 WHERE code = 'lifetime'")
sent = []
billing._notify = lambda email, subject, text: sent.append((email, subject, text))
billing._paystack = lambda method, path, **kw: {"data": {"access_code": "AC_" + str(time.time_ns()), "authorization_url": "https://paystack.test/x"}}


def user(tag):
    c = TestClient(app)
    email = f"lt{tag}{int(time.time() * 1000)}@example.com"
    r = c.post("/api/auth/signup", json={"email": email, "password": "Passw0rd!long1", "name": tag, "workspace_name": f"LT {tag}"})
    assert r.status_code == 200, r.text
    db.execute("UPDATE users SET email_verified_at = now() WHERE email=%s", (email,))
    ws = db.one("SELECT m.workspace_id FROM memberships m JOIN users u ON u.id=m.user_id WHERE u.email=%s", (email,))["workspace_id"]
    return c, ws, email


def pay(ref):
    row = db.one("SELECT amount_minor, currency FROM payments WHERE reference=%s", (ref,))
    return billing.apply_payment(ref, row["amount_minor"], row["currency"])


# ---- the plan and the switch
p = billing.get_plan("lifetime")
ok("the plan exists with the agreed limits", p and p["interval"] == "lifetime" and p["price_minor"] == 14900 and p["max_accounts"] == 4
   and p["max_connected"] == 1 and p["max_credits"] == 60 and p["max_members"] == 1 and p["max_ai_calls"] == -1 and p["max_reports"] == 0)
ok("it is never listed with the monthly and yearly plans", "lifetime" not in [x["code"] for x in billing.list_plans()] and "lifetime" not in [x["code"] for x in billing.list_plans(only_active=True)])
db.execute("DELETE FROM platform_settings WHERE key = 'lifetime_deal'")
ok("with nothing set it is on, capped at 200", billing.lifetime_config() == {"enabled": True, "cap": 200} and billing.lifetime_offer() is not None)
billing._pset("lifetime_deal", {"enabled": False, "cap": 200})
ok("an admin can switch it off, and then it is not offered anywhere", billing.lifetime_offer() is None and billing.public_pricing()["lifetime_offer"] is None)

ca, wa, ea = user("a")
ok("nobody can buy it while it is off", ca.post("/api/billing/checkout", json={"plan_code": "lifetime"}).status_code == 404)

billing.save_lifetime_config(enabled=True, cap=2)
o = billing.public_pricing()["lifetime_offer"]
ok("switched on, the page is offered the price and the spots left", o and o["price_minor"] == 14900 and o["cap"] == 2 and o["left"] == 2 and o["sold_out"] is False
   and o["includes"]["other_accounts"] == 1 and o["includes"]["youtube_accounts"] == 3 and o["includes"]["connected"] == 1)
ok("billing off means no offer", (billing.set_enabled(False), billing.lifetime_offer())[1] is None and (billing.set_enabled(True), True)[1])

# ---- checkout, the cap and pending reservations
r = ca.post("/api/billing/checkout", json={"plan_code": "lifetime", "save_card": True})
EXPECT = billing.charge_for(billing.get_plan("lifetime"))
ok("a workspace can start the lifetime checkout", r.status_code == 200 and (r.json()["amount_minor"], r.json()["currency"]) == EXPECT)
ref_a = r.json()["reference"]
row = db.one("SELECT amount_minor, save_card, status FROM payments WHERE reference=%s", (ref_a,))
ok("it is one payment of the full price, with no saved card", row["amount_minor"] == EXPECT[0] and row["save_card"] is False and row["status"] == "pending")
cb, wb, eb = user("b")
r = cb.post("/api/billing/checkout", json={"plan_code": "lifetime"}); ref_b = r.json()["reference"]
cc, wc, ec = user("c")
ok("a checkout in progress holds a spot, so the last spots are not oversold", cc.post("/api/billing/checkout", json={"plan_code": "lifetime"}).status_code == 409)
ok("the offer shows nothing left for others but still some for the people mid-checkout", billing.lifetime_left() == 0 and billing.lifetime_left(excluding_ws=wa) == 1)

# ---- the purchase
out = pay(ref_a)
sub = billing.subscription(wa)
ok("a paid lifetime checkout grants the plan with no end date", out["applied"] and out["lifetime"] and sub["plan_code"] == "lifetime" and sub["current_period_end"] is None and sub["lapsed"] is False)
ok("the workspace is in a paid state and never lapses", billing.workspace_state(wa)["state"] == "paid")
st = billing.status(wa)
ok("its limits are the finite lifetime ones", st["limits"]["accounts"] == 4 and st["limits"]["connected"] == 1 and st["limits"]["credits"] == 60 and st["limits"]["members"] == 1 and st["limits"]["ai_calls"] == -1)
ok("a sale is counted", billing.lifetime_sold() == 1 and db.one("SELECT COUNT(*) AS n FROM lifetime_grants WHERE workspace_id=%s", (wa,))["n"] == 1)
ok("paying twice for the same reference does nothing the second time", pay(ref_a)["applied"] is False)
ok("the buyer is emailed a receipt that says it is lifetime", any("lifetime access is active" in t for _, _, t in sent) and billing.has_lifetime(wa))
ok("a lifetime workspace cannot start another lifetime checkout", ca.post("/api/billing/checkout", json={"plan_code": "lifetime"}).status_code == 400)
ok("and cannot replace it with a monthly plan by mistake", ca.post("/api/billing/checkout", json={"plan_code": "pro"}).status_code == 400)
ok("its own billing page carries no offer", ca.get("/api/billing").json()["has_lifetime"] is True and ca.get("/api/billing").json()["lifetime_offer"] is None)

# ---- the account rule: 1 TikTok or Instagram account + up to 3 YouTube channels
mk = lambda h, plat: ca.post("/api/accounts", json={"handle": h, "platform": plat})
ok("the first TikTok account is allowed", mk("@one", "TikTok").status_code == 200)
r = mk("@two", "Instagram")
ok("a second TikTok or Instagram account is refused with a clear message", r.status_code == 402 and "1 TikTok or Instagram account" in r.json()["detail"])
ok("three YouTube channels are allowed", all(mk(f"@yt{i}", "YouTube").status_code == 200 for i in range(3)))
r = mk("@yt4", "YouTube")
ok("a fourth is over the plan's four tracked accounts", r.status_code == 402)
yt_id = db.one("SELECT id FROM accounts WHERE workspace_id=%s AND handle='@yt0'", (wa,))["id"]
r = ca.patch(f"/api/accounts/{yt_id}", json={"platform": "TikTok"})
ok("switching a YouTube channel to TikTok cannot get around the rule", r.status_code == 402)
ok("the usage the page shows matches", billing.usage(wa)["accounts"] == 4)

# ---- add-ons
ok("the small tracked-accounts add-on exists, is renewable and cheap", billing.ADDONS["accounts5"]["unit"] == 5 and billing.addon_price_minor("accounts5") == 300 and "accounts5" in billing.RENEWABLE)
r = ca.post("/api/billing/addons/checkout", json={"kind": "accounts5", "qty": 1})
ok("a lifetime buyer can buy it", r.status_code == 200)
billing.apply_payment(r.json()["reference"], r.json()["amount_minor"], r.json()["currency"])
ok("five more tracked accounts raise the limit to nine", billing.limit_for(wa, "accounts") == 9)
ok("and the extra TikTok or Instagram room opens up", mk("@two", "Instagram").status_code == 200)
r = ca.post("/api/billing/addons/checkout", json={"kind": "connected", "qty": 1})
billing.apply_payment(r.json()["reference"], r.json()["amount_minor"], r.json()["currency"])
ok("an extra publishing account is also an add-on", billing.limit_for(wa, "connected") == 2)

# ---- sold out
pay(ref_b)
o = billing.lifetime_offer()
ok("when the cap is reached it says sold out", billing.lifetime_sold() == 2 and o["left"] == 0 and o["sold_out"] is True)
ok("a new buyer is refused once it has sold out", cc.post("/api/billing/checkout", json={"plan_code": "lifetime"}).status_code == 409)

# ---- the admin console
adm.app.dependency_overrides[adm.require_admin] = lambda: adm.Admin(id=1, email="a@example.com", name="A")
ac = TestClient(adm.app)
sett = ac.get("/api/settings").json()["lifetime"]
ok("the admin console shows the count and the buyers", sett["enabled"] is True and sett["cap"] == 2 and sett["sold"] == 2 and sett["left"] == 0 and len(sett["grants"]) == 2 and sett["plan"]["price_minor"] == 14900)
ok("the cap cannot go below what has been sold", ac.put("/api/settings/lifetime", json={"cap": 1}).status_code == 400)
r = ac.put("/api/settings/lifetime", json={"cap": 200, "price_usd": 129})
ok("the admin can raise the cap and change the price", r.status_code == 200 and r.json()["left"] == 198 and billing.get_plan("lifetime")["price_minor"] == 12900)
db.execute("UPDATE plans SET price_minor = 14900 WHERE code = 'lifetime'")
ok("the plan cannot be assigned by hand", ac.put(f"/api/workspaces/{wc}/subscription", json={"plan_code": "lifetime", "status": "active"}).status_code == 400)

# ---- a refund
r = ac.post(f"/api/lifetime/{wa}/revoke", json={"reason": "refunded within 60 days"})
ok("recording a refund frees the spot", r.status_code == 200 and r.json()["sold"] == 1 and r.json()["left"] == 199)
ok("the workspace loses the lifetime plan", billing.has_lifetime(wa) is False and billing.subscription(wa)["plan_code"] == "free" and billing.workspace_state(wa)["state"] != "paid")
ok("refunding twice is refused", ac.post(f"/api/lifetime/{wa}/revoke", json={}).status_code == 404)
ok("the refunded buyer could buy again if you let them", ca.post("/api/billing/checkout", json={"plan_code": "lifetime"}).status_code == 200)

# ---- switching it off stops sales but keeps what was sold
billing.save_lifetime_config(enabled=False)
ok("switching it off hides it and stops checkout, and keeps existing buyers", billing.lifetime_offer() is None and cc.post("/api/billing/checkout", json={"plan_code": "lifetime"}).status_code == 404 and billing.has_lifetime(wb))
ok("monthly renewals never touch a lifetime workspace", db.one("SELECT COUNT(*) AS n FROM workspace_subscriptions WHERE plan_code='lifetime' AND current_period_end IS NOT NULL")["n"] == 0)

db.execute("DELETE FROM lifetime_grants"); billing._pset("lifetime_deal", {"enabled": False, "cap": 200})
for w in (wa, wb, wc):
    db.execute("DELETE FROM workspaces WHERE id=%s", (w,))
print("ALL OK")
