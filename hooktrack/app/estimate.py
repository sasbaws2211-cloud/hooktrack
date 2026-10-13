"""A first-week estimate of an account's daily views, from what its videos have reached so far.

The exact daily number comes from snapshots (today's total per video minus the previous one) and only exists from the
second day of syncing. Until then the grid is empty, so for the last 7 days each recently posted video's views are spread
over the days since it was posted using a typical short-form curve. Cells are tagged "estimated", never replace anything
the user typed or read from a screenshot, and are overwritten by the real formula as soon as it has a baseline.
Views that older videos gained in those days are not known, so an estimate can only undercount.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from . import db, logic

WEIGHTS = [0.35, 0.25, 0.15, 0.10, 0.07, 0.05, 0.03]      # share of a video's first week earned on day 0, 1, 2 ...
WINDOW = len(WEIGHTS)


def fill(ws: int, account_id: int) -> int:
    """Write estimated cells for empty days of the last week. Returns how many were written."""
    today = logic.today(ws)
    start = today - timedelta(days=WINDOW - 1)
    posts = db.query(
        """SELECT post_date, views FROM posts
           WHERE workspace_id=%s AND account_id=%s AND external_id IS NOT NULL AND NOT date_estimated
             AND post_date BETWEEN %s AND %s AND views > 0""", (ws, account_id, start, today))
    est: dict = defaultdict(float)
    for p in posts:
        age = (today - p["post_date"]).days
        share = sum(WEIGHTS[: age + 1])          # the part of the curve its views so far correspond to
        for k in range(age + 1):
            est[p["post_date"] + timedelta(days=k)] += p["views"] * WEIGHTS[k] / share
    have = {r["day"] for r in db.query(
        "SELECT day FROM daily_views WHERE account_id=%s AND day BETWEEN %s AND %s", (account_id, start, today))}
    written = 0
    for day, v in est.items():
        if day in have or day < start or day > today or v < 1:
            continue
        db.execute(
            """INSERT INTO daily_views (account_id, workspace_id, day, views, source, updated_at)
               VALUES (%s,%s,%s,%s,'estimated',now()) ON CONFLICT (account_id, day) DO NOTHING""",
            (account_id, ws, day, int(round(v))))
        written += 1
    return written
