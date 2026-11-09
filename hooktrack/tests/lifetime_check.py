import os, sys, json, time, hmac, hashlib
os.environ.setdefault("DATABASE_URL", "postgresql://hooktrack:ht_dev_pw@127.0.0.1:55434/hooktrack")
os.environ["APPSUMO_API_KEY"] = "testkey"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from app import db, billing, lifetime, appsumo
from app.main import app
db.init_schema()
from app import auth as _auth
billing._pset(_auth.SETTING_SIGNUP_IP_DAILY, 1000)
c = TestClient(app)
def hook(ev, key, tier=1, prev=None, test=False, bad=False):
    body = json.dumps({"event": ev, "license_key": key, "tier": tier, "prev_license_key": prev, "test": test}).encode()
    ts = str(int(time.time()))
    sig = "0"*64 if bad else appsumo.sign("testkey", ts, body)
    return c.post("/api/appsumo/webhook", content=body, headers={"X-Appsumo-Timestamp": ts, "X-Appsumo-Signature": sig})
ok = lambda n, cond: print(("PASS " if cond else "FAIL ") + n) or (cond or sys.exit(1))
sfx = str(int(time.time()))
email = f"ltd{sfx}@example.com"
r = c.post("/api/auth/signup", json={"email": email, "password": "Passw0rd!long1", "name": "L", "workspace_name": "LTD "+sfx})
print(r.status_code, r.text[:200])
ws = db.one("SELECT workspace_id FROM memberships m JOIN users u ON u.id=m.user_id WHERE u.email=%s", (email,))["workspace_id"]
billing.set_enabled(True); lifetime.set_mode("lifetime")
try:
    db.execute("DELETE FROM platform_settings WHERE key='billing_mode'"); ok("default mode is both", lifetime.mode() == "both"); lifetime.set_mode("lifetime")
    ok("no trial: new workspace read-only", billing.workspace_state(ws)["state"] == "expired" and billing.workspace_state(ws)["reason"] == "no_licence")
    ok("trial days forced 0", billing.trial_config()["days"] == 0)
    s0 = lifetime.spots(); ok("spots default cap 100", s0["cap"] == 100)
    ok("bad signature -> 401", hook("purchase", "K1"+sfx, bad=True).status_code == 401)
    ok("test ping acks, no row", hook("purchase", "T"+sfx, test=True).json()["success"] and not lifetime.get("T"+sfx))
    ok("purchase", hook("purchase", "K1"+sfx, 1).json()["success"] and lifetime.get("K1"+sfx)["status"] == "inactive")
    ok("duplicate purchase idempotent", hook("purchase", "K1"+sfx, 1).status_code == 200)
    ok("add-on blocked without licence", c.post("/api/billing/addons/checkout", json={"kind": "connected", "qty": 1}).status_code == 402)
    ok("redeem unknown 404", c.post("/api/billing/lifetime/redeem", json={"license_key": "nope"}).status_code == 404)
    ok("redeem ok", c.post("/api/billing/lifetime/redeem", json={"license_key": "K1"+sfx}).status_code == 200)
    ok("state paid, plan ltd_1", billing.workspace_state(ws)["state"] == "paid" and billing.effective_plan(ws)["code"] == "ltd_1")
    ok("redeem again idempotent", c.post("/api/billing/lifetime/redeem", json={"license_key": "K1"+sfx}).status_code == 200)
    r1 = c.post("/api/billing/addons/checkout", json={"kind": "credits", "qty": 1}); ok("credits add-on refused", r1.status_code == 400 and "publishing" in r1.text)
    r2 = c.post("/api/billing/addons/checkout", json={"kind": "connected", "qty": 1}); ok("publishing add-on allowed (reaches payment step)", "Only extra publishing" not in r2.text and r2.status_code != 402)
    ok("checkout blocked", c.post("/api/billing/checkout", json={"plan_code": "ltd_1"}).status_code == 400)
    b = c.get("/api/billing").json(); ok("billing status lifetime", b["mode"] == "lifetime" and b["lifetime"]["license"]["tier"] == 1)
    pp = c.get("/api/public/pricing").json()["plans"]; ok("two plans $48.80 / $97.60", [x["price_minor"] for x in pp] == [4880, 9760])
    ok("upgrade moves workspace", hook("upgrade", "K2"+sfx, 2, "K1"+sfx).status_code == 200 and billing.effective_plan(ws)["code"] == "ltd_2")
    ok("old key superseded", lifetime.get("K1"+sfx)["status"] == "superseded")
    ok("deactivate old key keeps access", hook("deactivate", "K1"+sfx).status_code == 200 and billing.workspace_state(ws)["state"] == "paid")
    ok("spots count excludes superseded", lifetime.sold() == s0["sold"] + 1)
    ok("refund ends access", hook("deactivate", "K2"+sfx).status_code == 200 and billing.workspace_state(ws)["state"] == "expired")
    ok("redeem refunded 400", c.post("/api/billing/lifetime/redeem", json={"license_key": "K2"+sfx}).status_code == 400)
    ok("callback no code 200", c.get("/appsumo/callback").status_code == 200)
    ok("events logged", db.one("SELECT COUNT(*) n FROM appsumo_events WHERE license_key LIKE %s", ("%"+sfx,))["n"] >= 5)
    # ---- combined mode: card plans + trial for direct customers, AppSumo codes redeemable ----
    os.environ["APPSUMO_BUY_URL"] = "https://appsumo.com/x"
    lifetime.set_mode("both")
    c2 = TestClient(app)
    e2 = f"both{sfx}@example.com"
    c2.post("/api/auth/signup", json={"email": e2, "password": "Passw0rd!long1", "name": "B", "workspace_name": "BOTH "+sfx})
    ws2 = db.one("SELECT workspace_id FROM memberships m JOIN users u ON u.id=m.user_id WHERE u.email=%s", (e2,))["workspace_id"]
    pb = c2.get("/api/public/pricing").json()
    ok("both: normal plans shown", pb["mode"] == "both" and len(pb["plans"]) >= 1 and all(x["interval"] != "lifetime" for x in pb["plans"]))
    ok("both: AppSumo offer shown with buy link", pb["lifetime_offer"] and pb["lifetime_offer"]["buy_url"] == "https://appsumo.com/x" and pb["lifetime_offer"]["price_minor"] == 4880)
    ok("both: trial for a new workspace", billing.workspace_state(ws2)["state"] == "trial" and pb["trial_days"] > 0)
    bs = c2.get("/api/billing").json(); ok("both: billing shows plans and code box data", bs["mode"] == "both" and len(bs["plans"]) >= 1 and bs["has_licence"] is False)
    hook("purchase", "K3"+sfx, 1)
    ok("both: redeem works on a trial workspace", c2.post("/api/billing/lifetime/redeem", json={"license_key": "K3"+sfx}).status_code == 200 and billing.effective_plan(ws2)["code"] == "ltd_1")
    r = c2.post("/api/billing/checkout", json={"plan_code": "solo"}); ok("both: licence holder cannot buy a card plan", r.status_code == 400 and "lifetime licence" in r.text)
    r = c2.post("/api/billing/addons/checkout", json={"kind": "credits", "qty": 1}); ok("both: licence holder cannot buy credits", r.status_code == 400)
    os.environ.pop("APPSUMO_BUY_URL", None)
    ok("both: no offer until the buy link is set", c2.get("/api/public/pricing").json()["lifetime_offer"] is None)
finally:
    lifetime.set_mode("subscriptions"); billing.set_enabled(False)
print("ALL OK")
