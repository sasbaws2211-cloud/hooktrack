# HookTrack

AI-native tracker for multi-account short-form content (a web app version of "The Ugly Tracker" spreadsheet).
FastAPI + Postgres + a dependency-free static UI.

## Run
```
docker compose up -d                       # Postgres on localhost:55434
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8030 # open http://localhost:8030 and create a workspace
```
Env: `DATABASE_URL`; `HOOKTRACK_SECRET` (encrypts stored API keys; if unset a `.secret` file is generated next to the app - back it up);
`HOOKTRACK_SECURE_COOKIES=1` behind HTTPS; `HOOKTRACK_ALLOWED_ORIGINS` for extra trusted hosts;
`ANTHROPIC_API_KEY` / `HOOKTRACK_MODEL` are optional server-wide defaults that any workspace can override.

## Multi-tenant
Each signup creates a private **workspace** (tenant). Every table carries `workspace_id` and every query is scoped by the
signed-in session's workspace. Roles: owner / admin / member; admins manage members, invites, AI and rules.
Invites are one-time links (7 days). A user can belong to several workspaces and switch from the header.
Data that existed before multi-tenancy is adopted by the first account that signs up.

## AI provider notes
- `HOOKTRACK_AI_TIMEOUT` (seconds, default 150): a 550B reasoning model took up to ~65 s for a week plan.
- Transient provider errors (429/500/502/503/504) are retried twice with a short backoff (`Retry-After` honoured, capped at 10 s); client errors (400/401/404) are not.
- Verified with NVIDIA NIM (`https://integrate.api.nvidia.com/v1`, model `nvidia/nemotron-3-ultra-550b-a55b`) as an OpenAI-compatible endpoint: connection test, scoring, hook ideas, week plan, view extraction from text, and ask-your-data all work. Not every model listed by NIM is callable (some return 404), so use **Test connection**.
- View extraction gets an explicit 14-day calendar in its prompt; a model otherwise mis-resolved "Monday" to a Tuesday.

## Accounts, password reset, email
- **Forgot password:** sign-in page → "Forgot your password?" sends a single-use link valid 1 hour. The answer is identical whether or not the email has an account, and a new request replaces the old link. Using a link signs the user out everywhere.
- **Email** is optional SMTP: set `HOOKTRACK_SMTP_HOST` (+ `HOOKTRACK_SMTP_PORT` 587, `HOOKTRACK_SMTP_USER`, `HOOKTRACK_SMTP_PASSWORD`, `HOOKTRACK_SMTP_FROM`, `HOOKTRACK_SMTP_SECURITY=starttls|ssl|none`) **and** `HOOKTRACK_PUBLIC_URL` (e.g. `https://hooktrack.example.com`; links in emails always use it, never the request's Host header). Without them nothing is mailed: the reset link is written to the server log at WARNING level (`password reset requested for … Link, valid 1h: …`) for the operator to pass on. Treat that log as sensitive.
- **Rate limits** live in Postgres (`rate_limits`), so they hold across workers and restarts. Only *failed* logins count: 8 per address+email, 40 per address, 30 per email (from any address) per 10 minutes; a good login clears the first. Forgot-password: 3 mails per email and 10 requests per address per hour.
- **Behind a reverse proxy** set `HOOKTRACK_TRUSTED_PROXIES=<n>` (how many proxies sit in front). The client address is then taken from `X-Forwarded-For` counting n entries from the right; with it unset the header is ignored (it is trivially spoofable). Also set `HOOKTRACK_SECURE_COOKIES=1` on HTTPS.
- **Housekeeping:** an hourly task removes expired sessions, reset tokens, old invites and rate-limit rows.
- **Delete a workspace:** Settings, owners only: needs the exact workspace name and the owner's password; removes all data and uploads.

## Database & migrations
`DATABASE_URL` (a `postgresql+asyncpg://` or `postgres://` style URL is accepted and rewritten for psycopg2).
Schema changes are versioned SQL files in `app/migrations/` (`0001_baseline.sql`, `0002_...`), applied once, in order, each in its own transaction, at startup (a Postgres advisory lock makes several workers booting at once safe). Applied versions and checksums live in `schema_migrations`; editing a shipped file is refused, so add the next numbered file instead.
```
python -m app.migrate status   # applied / pending
python -m app.migrate up       # apply pending without starting the app
```
Forward-only: to undo a change, restore a backup or write a new migration. Databases created before the runner existed upgrade in place (the baseline is idempotent). `workspace_id` is NOT NULL on phones/accounts/daily_views/posts once no orphan rows remain (legacy rows are claimed by the first signup, which then tightens the constraint).

## Time zones
Each workspace has an IANA time zone (**Settings → Time zone**, admins). It is set from the browser at signup and defaults to UTC for older workspaces. "Today", the default week (Mon-Sun), the Today screen, sample data, post dates assigned by sync, and the daily-gain day all follow it; the server clock's zone is never used. Needs the `tzdata` package on Windows (in requirements.txt).

## AI configuration
**Settings -> AI** (admins) picks a provider per workspace: **Anthropic**, **OpenAI**, or any **OpenAI-compatible** endpoint
(Groq, OpenRouter, Together, Mistral, vLLM, LiteLLM, Ollama ...). Key and model are stored per workspace (key encrypted, never returned),
with a Test connection button. Server-wide defaults: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` (never used for custom endpoints).
- Changing provider or endpoint drops the saved key, so a key is never sent to a different destination.
- Custom endpoints are validated and re-checked on every call; private/loopback/link-local addresses are blocked (SSRF).
  On a trusted single-tenant host set `HOOKTRACK_ALLOW_PRIVATE_AI_URLS=1` to allow e.g. `http://localhost:11434/v1`.
- Screenshot reading needs a vision-capable model; every feature needs tool/JSON-output support (a JSON-in-text fallback is built in).

## First-time setup
**Posts** is the main content UI with a **Board | Calendar** toggle (same posts). Settings split into **Getting started** (AI, rules, import) and collapsible **Add-ons** (SociaVault, video, publish).

**Today:** the daily-entry screen (`#/today`). Active accounts in a list with yesterday and 7-day average beside each box; type a number, Enter jumps to the next empty account, delta vs the 7-day average shows live. Shows days with missing views, and posts due (with Mark posted). Uses the browser's local date.

**P6:** Dark mode toggle, pin accounts, week movers, undo post delete.

**P5:** Duplicate post, CSV export (views/posts), dashboard recent activity, Scheduled nav count.

**P4:** Command palette (**Ctrl/⌘ K**), weekly summary copy, bulk stage move on Posts board (**Shift**+click).

**P3:** Setup progress in the header, **?** glossary, post-setup “what’s next” banner, stronger empty states, and **N** to new post on Hooks/Posts.

**P2:** One-time coach tips (dismissible, per workspace) and **Load sample data** on Get started for a quick aha without real accounts.

New workspaces see a **Get started** checklist (accounts → views → score a hook). Nav stays minimal until setup is complete; Pipeline, Calendar, Inspiration, and Scheduled appear afterward. Decisions are labeled **Scale / Tweak / Stop** (still stored as SCALE / MUTATE / KILL).

## What it does
- **Daily views**: account x day grid, type `85k` or paste/drop screenshots for AI entry (you review before saving).
- **Calendar**: scheduled posts by day; AI plans the next 7 days (from today, in the workspace time zone) from your winning hooks.
- **Pipeline (kanban)**: Idea → Scripted → Filmed → Posted → Analysed. Each card is the same `posts` row; drag between columns.
- **Hooks**: log each post, VSC score (manual or AI), SCALE 24+ / MUTATE 21-23 / KILL <21.
- **Social publish (add-on)**: BYOK Upload-Post / Ayrshare / Late. Explicit **Publish** on a post (caption + video_url). Never auto-posts.
- **Video generator (add-on)**: BYOK providers (Custom webhook, Shotstack, Creatomate, Mock). Generate from post script → job → `video_url` → stage Filmed.
- **Inspiration**: save competitor hooks (URL + screenshot + notes); AI hook generation uses them as few-shot examples when **Use in AI** is on.
- **Dashboard**: weekly totals, 8-week trend, by phone, what to scale/kill, and "ask your data".
- **Rules** (Accounts tab) make the spreadsheet's thresholds configurable.
- **Import** the original .xlsx (idempotent): `python -m app.import_xlsx <workspace_id> file.xlsx 2026-10-05` (or use the Accounts page).



## Winner detection (what a hook is judged on)
Raw views reward big accounts, so a post is judged against **its own account's normal**:
- **× normal** = views / median views of the account's other mature posts (last 30; YouTube Shorts and long-form are separate normals). **WINNER** at `winner_pct` (default 200 = 2x, editable in Rules), **DUD** at 0.5x or less, **AVERAGE** between. Posts under 2 days old are *too early* and never judged; fewer than 5 older posts means *no baseline* (no verdict rather than a guess).
- **Intent / 1k** = (saves + shares + comments) per 1,000 views: people who wanted to act, not just watch. An account's posts at 1.5x its own median intent get an *intent* chip. Likes are shown but not counted as intent.
- **Reach** (API only) = views / followers at posting time, from weekly follower snapshots.
- **Where it shows:** Hooks table (× normal, likes, saves, shares, intent/1k, *Winners only* filter), Dashboard top hooks, `GET /api/winners` and `perf` on `GET /api/posts`.
- **Where it feeds back:** *AI ideas* and *AI plan week* are shown the account's own winners and duds with the reason ("8.0x this account's normal, 120 saves, 12 intent signals per 1k views"), then other accounts' to fill up. Until a workspace has enough history for baselines they fall back to raw views.
- **Followers:** stored per account in `account_snapshots` (Accounts page shows count and 7-day change). Full sync always refreshes; light sync refreshes about weekly (`HOOKTRACK_FOLLOWER_SNAPSHOT_HOURS`, default 168), costing one extra credit then.

### What people asked for (comment-text intent)
Likes say people watched; comments like "where can I buy this?" say they wanted it. **Post → Read comments** (about 2 credits: 1 per ~15-20 comments, top comments first) classifies each comment with transparent rules (`app/intent.py`):
- **wants it** (`buy`): asks the price or where to buy, asks for a restock or drop, "I need this", "I'll buy", "link please".
- **asks** (`ask`): what app/brand is it, how did you do that, what size, can you share the details.
- **wants more** (`more`): part 2, make a series. Format demand, not product demand.
The creator's own replies and YouTube reply threads are skipped. Per post you get "4 of 17 comments ask to buy it or how/where to get it" and the most-liked quotes; the *Asking* column on Hooks shows it, and the AI is told it in the audience's own words ("people wrote: 'when restock onyx'"). **Hooks → winners' comments** (`POST /api/comments/winners`) reads the best recent posts that have not been read this week. Nothing reads comments automatically. Known limit: a want aimed at a platform ("we want banners") reads like a want aimed at a seller; words alone cannot tell them apart, so you see the quote. Rules were tuned on real comments from a consumer brand; extend `BUY`/`ASK` in `app/intent.py` for your niche or language.

### Early velocity (act while a post is hot)
Each sync keeps a views snapshot per post per day. A post's **first-day views** (the last snapshot within the day after posting) are compared with that account's usual first day (median of 5+ older posts). At the winner threshold the post is marked **🔥 HOT_START** while it is still under 2 days old and not otherwise judged: make variations now. The post panel shows views by day since posting; `GET /api/velocity` lists recent posts hottest first; Hooks shows a *Hot starts* card. It needs the daily sync to have run on the day of posting or the next (no snapshot, no early read; never a guess).

### Entering accounts
Type a handle, `@handle`, or paste a profile or video link (`tiktok.com/@name/video/...`, `instagram.com/name`, `youtube.com/@name`): the platform is detected and the handle normalised. **Check it exists** (1 credit, `POST /api/accounts/lookup`) shows who the account is and its follower count, so a typo is caught before the first sync. The handle you enter is exactly what is queried.

### Scale / Tweak / Stop on a post: what it rests on
A hook score is a *prediction*; once a post has been measured against its own account's normal, the result is better evidence. So the post-level decision is:
| Measured result | Decision | Why |
|---|---|---|
| WINNER (>= `winner_pct`, default 2x normal) | **Scale** | repeat it, whatever the hook score said |
| about normal | **Tweak** | it did its job: refine it, never throw it away |
| DUD (<= 0.5x normal) | **Stop** | drop this angle, even if the wording scored well |
| young, strong first day (HOT_START) | **Scale** | act while it is hot |
| not measured (too early / under 5 older posts) | from the hook score | the only evidence there is (24+ Scale, 21-23 Tweak, below Stop by default) |
Every row on Hooks says which it is ("measured" / "hook score" / "fast start", with the numbers on hover); the hook-score decision is still returned as `vsc_decision`. Posts imported by sync get a decision with no manual scoring.

**Fit the lines to your data, without circularity.** *Hooks → Simulate thresholds* compares your hook scores with how the posts actually did and, with 12+ measured posts (3+ winners, 3+ non-winners), suggests Scale/Tweak lines: the winners' lower quartile, kept above where ordinary posts score, with how many winners each setting catches and how many of what it flags are real winners. It uses **only blind scores**, i.e. made without knowing the result:
- your own scores (manual or imported), and
- AI scores made in *wording only* mode, where the model gets the hook, caption and format and **nothing about the post's own views, comments, saves or measured results** (the calibration examples also drop their results).
An ordinary AI score on a measured post is *informed*: the model is told the result, so it partly repeats it (one post scored 12 on its wording and 26 once told it was 14.8x normal). Those scores are kept for display and history but **never used for the fit**, nor are AI scores from before this was tracked (origin unknown). Each AI score records which kind it was; the post panel and Hooks mark it ("wording only" / "saw the results" / "your score").
- **Score wording only** (post panel) or `POST /api/ai/score/{id}?blind=true` replaces the visible score with a blind one.
- **Score the wording of N measured posts** (in Simulate thresholds) or `POST /api/ai/score-blind?limit=1..5` blind-scores measured posts one call at a time, winners and duds interleaved, and **never replaces a score you can already see** (the blind score goes to the post's history and is what the fit reads). Posts with no results yet are blind by nature.
The panel says how many posts the fit rests on, how many scored posts it left out and why, and if blind scores do not separate winners from the rest (under 2 points) it tells you the wording is not what separates them. If it cannot tell, trust measured results over the score. `GET /api/decisions/suggest`; one click saves the lines as your rules (admins).

### How the AI score works
**✨ AI score** (post panel, Hooks table, `POST /api/ai/score/{id}`) rates V/S/C out of 10 each with a confidence and written reasoning, and stores a history snapshot. It is given the hook, caption and format, the account's niche, 12 recent posts for calibration, and, when the post has been measured, **measured results as the primary evidence**: × the account's normal, likes/saves/shares, saves+shares+comments per 1k views, how many comments ask to buy it, and the most-liked of those comments. A post that clearly beat its account's normal therefore cannot score as a dud on the strength of its wording alone; an unmeasured post (too early, or under 5 older posts) is judged on wording with low confidence and says so. Thinking models that miss the structured answer are retried once with a bigger token budget. Expect a few points of run-to-run variation (the real model gave 21-24 on the same post); the thresholds (Scale 24+, Tweak 21-23) are editable under Rules and previewable with *Simulate thresholds*. Because the prompt calls 5-7 "typical", an ordinary hook totals about 15-21 and lands under Tweak: tune the thresholds to your own spread.

## Richer scoring & experiments
- AI scores store **confidence** (0–1), short **notes**, and full **reasoning** on the post.
- Every score (AI or manual VSC change) writes a row to **score_snapshots** with the decision and thresholds at that moment (`GET /api/posts/{id}/scores`).
- **Simulate thresholds** on the Hooks page: preview how many SCALE/MUTATE/KILL labels would flip if `scale_vsc` / `mutate_vsc` changed (`GET /api/decisions/preview`).
- **A/B hook experiments**: same body, different first-line hooks (`POST /api/experiments`). Variants are linked posts with `variant_label` A/B/C and shared `experiment_id`.

## SociaVault (platform sync)
Optional read-only pull of public posts & view counts from **TikTok**, **Instagram**, and **YouTube**.

1. Get an API key at [sociavault.com](https://sociavault.com) (one-time credit packs; most calls cost 1 credit).
2. **Settings → SociaVault** — paste the key (admins only). Or set server-wide `SOCIAVAULT_API_KEY`.
3. On each account, enable **Auto-sync** (Edit account).
4. Manual: **Sync** (one account) or **Sync all** (this workspace’s Auto-sync accounts).
5. **Scheduled (default):** once a day the server runs a **light** sync across **all workspaces** — every active account with Auto-sync on. ~1 credit per account (one videos page; upsert views; skip profile).

### Several workers / instances
`uvicorn --workers N` (or several containers on one database) is safe. Background jobs coordinate through Postgres, never process memory:
- **Scheduled sync:** every worker ticks every 5 minutes, but a run needs the `sociavault-scheduled-sync` lease (`job_leases`), and "is a run due?" is judged by the *last finished run anywhere* (`platform_settings.sync_last_run`), so N workers still produce one sync per interval and a restart doesn't trigger an extra one. The lease auto-expires 10 minutes after a crash (a heartbeat renews it while a long run is alive).
- **Per account:** each account is claimed atomically (`accounts.sync_claimed_at`) together with its freshness check before any credits are spent, so manual **Sync**, **Sync all**, the schedule and the cron endpoint can never sync the same account at once (a manual attempt gets HTTP 409). A crashed sync's claim is taken over after 10 minutes.
- **Video polling** runs under a `video-poll` lease; **publish** jobs were already claimed atomically; cleanup and recovery sweeps are idempotent.
- `GET /api/sync/schedule` (and the platform console) report the same last run and live "running" state from every worker.
- Turning the schedule on/off at runtime (platform console) now takes effect without a restart.

### Scheduler env
| Env | Default | Meaning |
|-----|---------|---------|
| `HOOKTRACK_SYNC_INTERVAL_HOURS` | `24` | Hours between runs. `0` disables the in-process scheduler. |
| `HOOKTRACK_SYNC_MODE` | `light` | `light` (cheap daily) or `full` (profile + more pages). |
| `HOOKTRACK_SYNC_DAYS_BACK` | `7` | How far back to consider posts. |
| `HOOKTRACK_CRON_SECRET` | unset | If set, `POST /api/sync/scheduled` with header `X-HookTrack-Cron-Secret` runs the job (for external cron / multi-worker). |
| `SOCIAVAULT_API_KEY` | unset | Server-wide key used when a workspace has none. |

`GET /api/sync/schedule` (admin) shows last run. Accounts already synced within ~85% of the interval are skipped unless `force=1`.

Synced posts land as `status=posted` with `external_id` set (idempotent). Each sync stores a per-post lifetime-views snapshot; the daily grid gets the views **gained** today (snapshot minus the previous one, `source=sociavault`). The first sync of an older post has no baseline, so it adds nothing for that day. Cells entered by hand or by AI screenshot are never overwritten by sync.
An account is only marked KILL when its last N posts all have recorded views; otherwise it stays HOLD with a "not enough data" reason.
**What a sync stores per post:** views, comments, likes, shares, saves, duration (seconds), caption, publish date. Each platform fills what it exposes (TikTok: likes/shares/saves/duration; Instagram: likes/duration; YouTube: likes/comments/duration); a metric the platform omits stays NULL and never overwrites a stored one. **Page sizes:** TikTok 10 posts per page, Instagram 12, YouTube 30 long-form / 48 Shorts. Light mode reads page 1 only (1 credit), so an account posting more than ~10 times in the window will have older posts that are not refreshed; use Full sync (up to 3 pages) for those. **YouTube** pulls the Shorts endpoint first (the long-form endpoint contains no Shorts); light mode stops there unless the channel has none, then falls back to long-form (an extra credit). Pinned TikTok posts are old by design and no longer end pagination.
**Verified against the live API (2026-10-10):** TikTok (`aweme_list`), Instagram (`items[].media`) and YouTube (`items[]` with `viewCountInt`) responses all parse. YouTube gives no publish time, so those posts are dated "first seen" and flagged `date_estimated`: a sync records their views but never treats lifetime views as "gained today"; real daily gains start from the second day of syncing.
Public data only — no private Insights / reach. Keep screenshot AI entry and XLSX import as fallbacks.

## Tests
`python -m pytest tests` (needs the Postgres container; uses a separate `hooktrack_test` database; AI calls are mocked).

## Not built (on purpose)
No direct TikTok API pull (SociaVault is the connector). Invites are copy-paste links (email is only used for password reset). No email verification at signup.

## Video generator add-on
Optional. **Settings → Video generator** (admins): enable, pick provider, paste key / webhook URL.

| Provider | Needs |
|----------|--------|
| **Runway (AI)** | API key — text-to-video from hook/body |
| **Luma (AI)** | API key — Dream Machine generation |
| **Replicate (AI)** | API token — default Kling (configurable model) |
| Custom webhook | URL that accepts POST `{hook, body, caption, account, job_id}` and returns `{video_url}` or `{job_id, poll_url, status}` |
| Shotstack | API key; builds a simple title/subtitle MP4 from hook + body |
| Creatomate | API key + template ID |
| Mock | Instant placeholder URL (dev) |

On a post: **🎬 Video** starts a job. Background poll every ~45s. `GET /api/posts/{id}/video` for status.
Env: `HOOKTRACK_VIDEO_API_KEY` (optional server default), `HOOKTRACK_ALLOW_PRIVATE_VIDEO_URLS=1` for localhost webhooks.

## Social publish add-on
Optional. **Settings → Social publish**: enable, pick provider, paste API key.

| Provider | Notes |
|----------|--------|
| **Upload-Post** | Best pricing; connect accounts in their dashboard |
| **Ayrshare** | Mature multi-network API; optional Profile-Key |
| **Late** | API-first alternative |
| **Mock** | Dev only |

On a post: **📤 Publish** → **Publish now** or **Schedule for** (datetime). Future times are queued (`status=queued`) and published automatically when due (~45s poll). **Scheduled** nav lists upcoming jobs (cancel supported).
Config `schedule_mode`: `local` (default, HookTrack queue) or `provider` (pass time to Ayrshare/Late/Upload-Post).
**Crash recovery:** a background sweep (every ~45s, `HOOKTRACK_PUBLISH_STALE_MINUTES`, default 10) handles jobs left `pending` by a restart. If the request had not yet gone to the provider, a scheduled job is re-queued and an immediate one is marked failed. If it *had* been sent, the job becomes `unconfirmed` and is never retried automatically (it might be live): it shows under **Scheduled → Needs your attention**, where you choose "It was posted" or "It wasn't posted". Publishing that post again is blocked until you do.
Connect your TikTok/IG/YouTube accounts **in the provider's UI**; HookTrack only calls their publish API with your key.


## Platform admin (standalone)

Separate console at **`/admin/`** for operators who manage the whole deployment (not a single workspace).

1. Set env: `HOOKTRACK_PLATFORM_ADMINS=you@company.com,ops@company.com`
2. Restart the server and sign in with that email
3. Open `/admin/` (or **Platform** in the app header)

### Capabilities
- Overview counts (users, workspaces, accounts, posts)
- List/search workspaces and users; workspace detail + **delete**
- System flags (schedule, whether server keys exist — secrets never shown)
- Trigger SociaVault sync and due publish/video jobs

Workspace roles (`owner` / `admin` / `member`) are unchanged; platform admin is email-based via env only.
