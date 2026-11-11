"""Fill the Discover feed by hand, one niche at a time, with a credit cap.

    python tools/discover_refresh.py --tiktok --niches fitness --max-credits 15   # TikTok: about 15 SociaVault credits per niche
    python tools/discover_refresh.py --youtube --niches fitness,food             # YouTube: about 300 free API units per niche
    python tools/discover_refresh.py --tiktok-on / --tiktok-off                  # let the scheduler refresh TikTok weekly, or not

TikTok costs SociaVault credits (search 1 + 1 per account measured), so a run never spends more than --max-credits and refuses to
start when the balance would drop under --reserve. The scheduler never refreshes TikTok on its own unless you run --tiktok-on, and
even then only while the balance stays above 300.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines() if (ROOT / ".env").exists() else []:
    m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line.strip())
    if m and m.group(1) not in os.environ:
        os.environ[m.group(1)] = m.group(2).strip().strip('"').strip("'")

from app import discover, sociavault, vaultwatch  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tiktok", action="store_true")
    ap.add_argument("--youtube", action="store_true")
    ap.add_argument("--niches", help="comma-separated; default is every niche")
    ap.add_argument("--max-credits", type=int, default=discover.TT_MAX_CREDITS, help="per niche (TikTok)")
    ap.add_argument("--reserve", type=int, default=0, help="credits that must remain after the run (TikTok)")
    ap.add_argument("--tiktok-on", action="store_true")
    ap.add_argument("--tiktok-off", action="store_true")
    a = ap.parse_args()
    if a.tiktok_on or a.tiktok_off:
        discover.set_tiktok_enabled(a.tiktok_on)
        print("TikTok discovery is now", "ON" if a.tiktok_on else "OFF")
        return 0
    niches = a.niches.split(",") if a.niches else list(discover.NICHE_TERMS)
    bad = [n for n in niches if n not in discover.NICHE_TERMS]
    if bad:
        ap.error(f"unknown niche {bad}; choose from {list(discover.NICHE_TERMS)}")
    if not a.tiktok and not a.youtube:
        ap.error("give --tiktok and/or --youtube")
    if a.tiktok:
        key = sociavault.resolve_server_key()
        balance = vaultwatch._fetch_credits(key)
        print(f"SociaVault balance {balance}")
        for n in niches:
            if balance - a.max_credits < a.reserve:
                print(f"stopping before {n}: it could take the balance under {a.reserve}")
                break
            res = discover.refresh_niche_tiktok(n, key, max_credits=a.max_credits)
            balance -= res["credits"]
            print(res)
    if a.youtube:
        for n in niches:
            print(discover.refresh_niche(n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
