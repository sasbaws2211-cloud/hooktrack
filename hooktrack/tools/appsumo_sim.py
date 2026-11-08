"""Send a signed AppSumo-style webhook to a running HookTrack, to try the lifetime flow without AppSumo.

  APPSUMO_API_KEY=... python tools/appsumo_sim.py http://localhost:8000 purchase --key LIC-123 --tier 2
  events: purchase | activate | upgrade | downgrade | deactivate   (use --prev for upgrade/downgrade, --test for a test ping)
"""
import argparse, hashlib, hmac, json, os, sys, time, urllib.request, urllib.error

ap = argparse.ArgumentParser()
ap.add_argument("base"); ap.add_argument("event")
ap.add_argument("--key", required=True); ap.add_argument("--tier", type=int, default=1)
ap.add_argument("--prev", default=""); ap.add_argument("--test", action="store_true")
a = ap.parse_args()
api_key = os.environ.get("APPSUMO_API_KEY", "")
if not api_key:
    sys.exit("set APPSUMO_API_KEY")
body = json.dumps({"event": a.event, "license_key": a.key, "tier": a.tier, "prev_license_key": a.prev or None,
                   "test": a.test, "license_status": a.event}).encode()
ts = str(int(time.time() * 1000))
sig = hmac.new(api_key.encode(), ts.encode() + body, hashlib.sha256).hexdigest()
req = urllib.request.Request(a.base.rstrip("/") + "/api/appsumo/webhook", data=body, method="POST",
                             headers={"Content-Type": "application/json", "X-Appsumo-Timestamp": ts, "X-Appsumo-Signature": sig})
try:
    r = urllib.request.urlopen(req); print(r.status, r.read().decode())
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode())
