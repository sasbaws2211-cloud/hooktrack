"""Sample data for first-time exploration (P2)."""
from __future__ import annotations

from datetime import date, timedelta

from . import db, logic


def seed_demo(workspace_id: int, *, force: bool = False) -> dict:
    """Populate a workspace with sample phones, accounts, views, and scored posts.

    Refuses if the workspace already has accounts unless force=True.
    """
    n = db.one("SELECT COUNT(*) AS n FROM accounts WHERE workspace_id=%s", (workspace_id,))["n"]
    if n and not force:
        raise ValueError("Workspace already has accounts. Clear them first, or pass force.")

    # phones
    p1 = db.one(
        "INSERT INTO phones (workspace_id, label, color) VALUES (%s,%s,%s) RETURNING id",
        (workspace_id, "Phone A", "#6366f1"),
    )["id"]
    p2 = db.one(
        "INSERT INTO phones (workspace_id, label, color) VALUES (%s,%s,%s) RETURNING id",
        (workspace_id, "Phone B", "#ec4899"),
    )["id"]

    accounts = [
        (p1, "@demo_fitness", "TikTok", "fitness", "active", "Before/after hooks"),
        (p1, "@demo_recipes", "TikTok", "food", "active", "30-second recipes"),
        (p2, "@demo_money", "Instagram", "finance", "active", "Money myths"),
    ]
    acc_ids = []
    for phone_id, handle, platform, niche, status, hook in accounts:
        row = db.one(
            """INSERT INTO accounts
               (workspace_id, phone_id, handle, platform, niche, status, content_hook, times_per_day)
               VALUES (%s,%s,%s,%s,%s,%s,%s,1) RETURNING id""",
            (workspace_id, phone_id, handle, platform, niche, status, hook),
        )
        acc_ids.append(row["id"])

    from .logic import today as _ws_today
    today = _ws_today(workspace_id)
    monday = today - timedelta(days=today.weekday())

    # daily views — last 14 days, different trajectories
    patterns = [
        [12_000, 15_000, 18_000, 22_000, 45_000, 38_000, 28_000, 25_000, 30_000, 55_000, 42_000, 35_000, 40_000, 48_000],
        [3_000, 4_000, 5_000, 4_500, 6_000, 5_500, 4_000, 3_500, 4_200, 5_000, 4_800, 3_900, 4_100, 4_500],
        [80_000, 95_000, 70_000, 110_000, 88_000, 92_000, 100_000, 75_000, 120_000, 98_000, 85_000, 90_000, 105_000, 99_000],
    ]
    for ai, series in zip(acc_ids, patterns):
        for i, views in enumerate(series):
            day = monday - timedelta(days=13 - i)
            db.execute(
                """INSERT INTO daily_views (workspace_id, account_id, day, views)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (account_id, day) DO UPDATE SET views=EXCLUDED.views, workspace_id=EXCLUDED.workspace_id""",
                (workspace_id, ai, day, views),
            )

    # posts with scores
    samples = [
        (acc_ids[0], monday - timedelta(days=2), "posted",
         "I lost 20lbs without cardio — here's the only change",
         "Show the before photo, then the one rule: protein at every meal.",
         9, 8, 8, 52_000),
        (acc_ids[0], monday + timedelta(days=1), "scripted",
         "Stop doing 10k steps if you want to lose fat",
         "Call out the myth, then give the 3 better metrics.",
         None, None, None, 0),
        (acc_ids[1], monday - timedelta(days=5), "posted",
         "3-ingredient pasta that tastes illegal",
         "Hook on the cheese pull, then the 45-second method.",
         6, 5, 5, 4_200),
        (acc_ids[2], monday - timedelta(days=1), "posted",
         "Your savings account is making you poorer",
         "Open with the inflation number, then the alternative.",
         9, 9, 8, 130_000),
        (acc_ids[2], monday + timedelta(days=2), "idea",
         "Why your 401k is not a retirement plan",
         "",
         None, None, None, 0),
    ]
    for n, (account_id, post_date, status, hook, body, v, s, c, views) in enumerate(samples):
        total = None if v is None else (v + s + c)
        db.execute(
            """INSERT INTO posts
               (workspace_id, account_id, post_date, status, hook, body, caption, views, v, s, c, score_by, score_notes, external_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                workspace_id, account_id, post_date, status, hook, body, hook[:80],
                views, v, s, c,
                "demo" if v is not None else "",
                "Sample scored post — explore Scale / Tweak / Stop on the dashboard." if v is not None else "",
                f"demo-{workspace_id}-{n}" if status == "posted" else None,     # posted samples stand in for synced posts
            ),
        )

    # mark seeded
    db.execute(
        """INSERT INTO settings (workspace_id, key, value) VALUES (%s, 'demo_seeded', %s)
           ON CONFLICT (workspace_id, key) DO UPDATE SET value=EXCLUDED.value""",
        (workspace_id, db.Json(True)),
    )

    return {
        "ok": True,
        "accounts": len(acc_ids),
        "phones": 2,
        "message": "Sample data loaded. Explore Dashboard, Views, Hooks, and Posts.",
    }


def clear_demo(workspace_id: int) -> dict:
    """Remove all content in the workspace (accounts cascade-ish — delete in order)."""
    # posts, views, inspiration, jobs first if no cascade on all
    try:
        db.execute("DELETE FROM score_snapshots WHERE post_id IN (SELECT id FROM posts WHERE workspace_id=%s)", (workspace_id,))
    except Exception:
        pass
    try:
        db.execute("DELETE FROM video_jobs WHERE workspace_id=%s", (workspace_id,))
        db.execute("DELETE FROM publish_jobs WHERE workspace_id=%s", (workspace_id,))
    except Exception:
        pass
    db.execute("DELETE FROM posts WHERE workspace_id=%s", (workspace_id,))
    db.execute("DELETE FROM daily_views WHERE workspace_id=%s", (workspace_id,))
    db.execute("DELETE FROM inspiration WHERE workspace_id=%s", (workspace_id,))
    db.execute("DELETE FROM accounts WHERE workspace_id=%s", (workspace_id,))
    db.execute("DELETE FROM phones WHERE workspace_id=%s", (workspace_id,))
    db.execute("DELETE FROM settings WHERE workspace_id=%s AND key='demo_seeded'", (workspace_id,))
    return {"ok": True, "message": "Sample data cleared."}
