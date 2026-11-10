"""Admin console endpoints for the YouTube key and the free plan. Run: python tests/youtube_admin_check.py (local test database)."""
import os, sys
os.environ.setdefault("DATABASE_URL", "postgresql://hooktrack:ht_dev_pw@127.0.0.1:55434/hooktrack")
os.environ.pop("YOUTUBE_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
from fastapi.testclient import TestClient
from app import billing, db, youtube_api as yt
import admin.main as adm

db.init_schema()
db.execute("DELETE FROM platform_settings WHERE key IN ('youtube_api_key_enc','youtube_daily_budget','free_plan')")
db.execute("DELETE FROM youtube_quota")
adm.app.dependency_overrides[adm.require_admin] = lambda: adm.Admin(id=1, email="a@example.com", name="A")
c = TestClient(adm.app)
ok = lambda n, cond: print(("PASS " if cond else "FAIL ") + n) or (cond or sys.exit(1))
KEY = "AIzaSyTESTKEYabcdefghijklmnop123456"

s = c.get("/api/settings").json()["youtube"]
ok("with no key the admin shows YouTube is not set up", s["configured"] is False and s["free_plan"]["enabled"] is True and s["free_plan"]["max_accounts"] == 3)
r = c.put("/api/settings/youtube", json={"api_key": "short"}); ok("a malformed key is refused", r.status_code == 400)
r = c.put("/api/settings/youtube", json={"api_key": KEY, "daily_budget": 5000, "free_accounts": 5, "free_members": 2, "free_ai": 50})
j = r.json()
ok("the key is saved encrypted and only its last four characters are shown", r.status_code == 200 and j["youtube"]["configured"] and j["youtube"]["source"] == "platform" and KEY not in r.text and j["youtube"]["key_hint"] == "…" + KEY[-4:])
stored = db.one("SELECT value FROM platform_settings WHERE key='youtube_api_key_enc'")["value"]
ok("the stored value is not the plain key", isinstance(stored, str) and KEY not in stored and yt.key() == KEY)
ok("the budget and free plan caps are saved", yt.budget() == 5000 and billing.free_plan_config() == {"enabled": True, "max_accounts": 5, "max_members": 2, "max_ai_calls": 50})
ok("the whole settings page never contains the key", KEY not in c.get("/api/settings").text)

yt._http = lambda: httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"items": [{"id": "UC" + "b" * 22, "snippet": {"title": "YouTube", "customUrl": "@youtube"}, "statistics": {}, "contentDetails": {"relatedPlaylists": {"uploads": "UU" + "b" * 22}}}]}) if req.url.params.get("key") == KEY else httpx.Response(403, json={})))
r = c.post("/api/settings/youtube/test").json(); ok("the test button reads a channel and reports quota used", r["ok"] and "YouTube" in r["message"] and "of 5000" in r["message"] and KEY not in str(r))
r = c.put("/api/settings/youtube", json={"free_enabled": False}).json(); ok("the free plan can be switched off", r["free_plan"]["enabled"] is False and billing.free_plan_on() is False)
r = c.put("/api/settings/youtube", json={"free_enabled": True, "free_ai": -1}).json(); ok("free AI can be unlimited", r["free_plan"]["max_ai_calls"] == -1)
r = c.put("/api/settings/youtube", json={"free_accounts": 500}); ok("out-of-range caps are refused", r.status_code == 422)
r = c.put("/api/settings/youtube", json={"clear_key": True}).json(); ok("the key can be removed", r["youtube"]["configured"] is False and yt.available() is False)
db.execute("DELETE FROM platform_settings WHERE key IN ('youtube_api_key_enc','youtube_daily_budget','free_plan')")
print("ALL OK")
