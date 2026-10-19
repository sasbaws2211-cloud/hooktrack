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
- **Email** is optional and sent through [Resend](https://resend.com): set `RESEND_API_KEY` and `HOOKTRACK_EMAIL_FROM` (an address on a domain verified in Resend, e.g. `HookTrack <hello@yourdomain.com>`) **and** `HOOKTRACK_PUBLIC_URL` (e.g. `https://hooktrack.example.com`; links in emails always use it, never the request's Host header). Without them nothing is mailed: the reset link is written to the server log at WARNING level (`password reset requested for … Link, valid 1h: …`) for the operator to pass on. Treat that log as sensitive.
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
**Posts** is the main content UI with a **Board | Calendar** toggle (same posts). Settings has **Getting started** (AI, rules, import) and **Publishing** (connect your TikTok, Instagram and YouTube accounts). SociaVault and the publishing connection are set once by the platform admin, not per workspace.

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
- **Pipeline (kanban)**: Idea → Scripted → Filmed → Posted → Analysed. Each card is the same `posts` row; drag between columns. Synced posts are on Hooks, not the board.
- **Hooks**: log each post, VSC score (manual or AI), SCALE 24+ / MUTATE 21-23 / KILL <21.
- **Publishing**: HookTrack's own Late (Zernio) connection, on by default once the platform admin has set its key. Users connect their own accounts in Settings → Publishing; an explicit **Publish** on a Filmed card posts it. Never auto-posts.
- **Videos are uploaded, not generated**: drag and drop or choose a file. (The video generator add-on was removed.)
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
- your own scores (manual or imported).
The panel says how many posts the fit rests on, how many scored posts it left out and why, and if blind scores do not separate winners from the rest (under 2 points) it tells you the wording is not what separates them. If it cannot tell, trust measured results over the score. `GET /api/decisions/suggest`; one click saves the lines as your rules (admins).

### AI scoring was removed
The ✨ AI score button and its endpoints (`/api/ai/score/{id}`, `/api/ai/score-blind`) were taken out: live tests showed a wording-only score does not separate winners from duds. Decisions come from measured results; V/S/C is still a manual score.

## Richer scoring & experiments
- Every score (manual VSC change) writes a row to **score_snapshots** with the decision and thresholds at that moment (`GET /api/posts/{id}/scores`).
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

## Social publish
Default: **Settings → Publishing** (HookTrack's own connection, see "Publishing through HookTrack's own Late account" below). Only if the platform admin has not set that key does a workspace fall back to its own provider key saved through the API (`PUT /api/addons/publish`); the Settings page no longer shows those fields.

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

A separate app for the operator of the whole deployment. It is its **own process, own login and own UI**, and shares only the database with the customer app, so it can sit on a private network or behind a VPN. The customer app serves no admin routes.

```bash
# first admin: from the environment (used only while no admin exists) ...
HOOKTRACK_ADMIN_EMAIL=you@company.com HOOKTRACK_ADMIN_PASSWORD='10+ characters'   python -m uvicorn admin.main:app --host 127.0.0.1 --port 8040
# ... or from the command line
python -m admin.create_admin you@company.com            # --reset to set a new password
```

Admins are rows in `platform_admins` (scrypt hashes, 12 h sessions, `SameSite=Strict` cookie, login rate limiting). They are not workspace users. Manage them under **Admins**.

**Console:** Overview (MRR, 30-day revenue, plan mix, AI usage) · Workspaces (plan, usage, grant a plan, rename, delete) · Plans · Payments · Users · Settings (billing, Paystack key, SociaVault, run sync / due posts) · Admins.

## Landing page

`/welcome` is the public front door (`static/landing.html`, one self-contained file). A visitor who is not signed in and opens `/` is sent there; its buttons go to `/#/signup` and `/#/login`, so invite and password-reset links, which live on `/`, are unaffected. Prices, the trial length and the "≈ local currency charged" line are read live from `GET /api/public/pricing`, so they always match the admin console; when billing is off the pricing section hides itself. The page makes no claims beyond what the product does and contains no testimonials or customer numbers. The pictures at the top are screenshots of the real Hooks page and Posts board (the Posts board sits slightly below and over the Hooks page) taken with invented sample data and labelled "Example data". Regenerate them after a UI change with `python tools/landing_shots.py`: it builds a throwaway sample workspace, signs in through the real login form and writes `static/shots/hooks.webp` and `posts.webp`; it never touches your own database.

## Matching videos: upload new videos that match a winning hook

On a **winning hook** (a measured winner, or a post you chose to Scale) the editor has two parts:

- **Winning Hook Video** is the *reference*: the original **played right in the editor** (press *Play the original here*; TikTok, Instagram and YouTube are embedded, with an *Open on TikTok ↗* link as a fallback), what is said in its first seconds, its sound and how many times its account's normal it did. Nothing is attached to the winner itself. The player is **click to play**: nothing is requested from the platform until you press play, YouTube is loaded from its privacy-enhanced domain, the iframe is sandboxed, and its address is rebuilt by the server from a validated video id on the platform's own embed route (a link that is not on the platform's domain is ignored). The Privacy Policy says that the platform may set its own cookies once you play. A matching post can play its winner too, to compare while editing.
- **Matching videos** is where new videos are added. Everything you upload there is a **new video built to match the winner**, and each becomes **its own post, linked to the winner** (labelled C1, C2, …, starting from the winner's hook and caption so you can edit it, schedule it and measure it like any other post). 
  - **Drag and drop** (or click to choose) your new video: MP4, MOV or WebM, up to 300 MB (`HOOKTRACK_MAX_VIDEO_MB`). Dropping on the winner creates the linked post with the file attached (`POST /api/posts/{id}/matching-video`).
  - **Uploaded, not generated.** A video generator is only handed the hook's text, which is too little to reproduce the winner, so there is no generate button. Make the video yourself and drop it here.

Each matching post's own editor shows **Matching video** ("Matches the winning hook video …") with its own drop zone, preview, Replace and Remove, and its own Publish button. The winner's list shows each one's state (uploaded, or script only) and, once measured, whether it won, with an **Open** button; the Hooks page scorecard totals how often matching videos win.

- **Checked by content**, not name (a renamed text or HTML file is refused, 415); size is capped while streaming (413); a refused drop leaves no empty post behind.
- **Private:** stored under `uploads/<workspace>/videos/<post>/` with a random name, never shown to the browser. The owner previews it through `GET /api/posts/{id}/video/file` (cookie and workspace checked, range requests supported). Other workspaces get a 404.
- **One video per post.** Uploading replaces any older video link on the post. Deleting the post, the file or the workspace removes the files.
- **Publishing:** providers have no login, so they download the file from a short-lived (48 h, longer for far-future schedules), HMAC-signed link `/media/v/...`, built fresh each time the publish runs and never stored in the job. That needs a public address: set `HOOKTRACK_PUBLIC_URL`. Without it, or when it points at localhost or a private network, publishing says exactly that. A post with a video and no caption can still be published.
- The uploaded file itself is not analysed (no frame or audio analysis); the winner's blueprint comes from its platform transcript and your note.

## Development mock providers

The publish add-on has a **mock** provider that returns an instant fake success (a placeholder `example.com` video, a fake "published") so the app can be built and tested offline. They are **off unless `HOOKTRACK_ALLOW_MOCK_ADDONS=1`** (the test suite sets it). On a normal server the mock is not offered in Settings, cannot be saved, and an add-on that was left on `mock` reports as *not set up*, so its 🎬 Video and 📤 Publish buttons are disabled with a reason instead of producing a fake result.

## Winner baseline

When a post is a measured winner (or you chose to Scale it), open it and use **Video baseline**. *Read the video* fetches its transcript from SociaVault (`tiktok/transcript`, `instagram/transcript`, `youtube/video/transcript`; confirmed live at 1 credit each, stored once; a video with no speech returns an empty transcript, and you can still add a note) and builds a **blueprint**: the spoken hook (the first speakable sentence), the beats in order with timestamps, the call to action, mechanics such as "opens with a question" or "speaks to you", the sound used (captured free during TikTok syncs), the caption pattern and what the post achieved. If AI is on it also describes the format, tone, what each beat does and what is worth keeping or varying, using only what the transcript and your note support. A short note field ("what is on screen") covers what a transcript cannot. **A video is read once:** an empty result (music-only or silent) is remembered and never charged again unless you ask (*Read again*), and *No speech: skip (free)* builds the baseline with no paid call at all. A silent video with no note also skips the AI structuring call, so it is instant.

The blueprint is a reading of how the winner is built, for you to follow when you make the matching video. Decisions are still made from measured results, never from this. Endpoints: `GET/POST /api/posts/{id}/baseline`, `PUT /api/posts/{id}/video-notes`, `GET /api/posts/{id}/clones` (the matching videos), `GET /api/clones/scorecard`. Use it on your own videos; for anyone else's, take the pattern, not their script.

There is no AI "clone script" writer and no *Make more like this* button: new videos come only from your own uploads or from your provider rendering the winner's script (below).

## Cookie notice

`static/cookie-consent.js` (loaded by the app, the landing page and the Terms and Privacy pages) shows a small notice, remembers the acknowledgement in the visitor's own browser (`localStorage`, nothing is sent to the server) and can be brought back from the "Cookie notice" footer link. HookTrack sets **one** cookie, the essential HttpOnly session cookie, and uses no advertising or analytics, so the notice is an acknowledgement rather than a set of toggles; a test asserts that claim (public pages set no cookies, and signing in sets exactly one). If you ever add an optional cookie or a tracker, list it in `CATEGORIES` and bump `VERSION` in that file: everyone is asked again and the banner switches to *Accept all / Essential only*, with `HookTrackCookies.allows('analytics')` to gate the code.

## Account deletion

Settings → **Delete my account** (`GET /api/auth/account/deletion-preview`, `DELETE /api/auth/account`) permanently removes a person. First it shows, per workspace, what will happen: a workspace they are the **only member** of is deleted with all its data, uploads, subscription and saved card; a workspace shared with others where another owner exists is simply left; if they are the **only owner of a shared workspace** deletion is refused until they make someone else an owner, or explicitly tick "also delete those workspaces for everyone". It needs the password and the exact email, is rate limited, signs them out, removes invitations addressed to them and emails a confirmation if email is set up. Payment records are kept for accounting (unlinked from any workspace) and the one-trial record is kept so deleting and re-registering does not give a second trial. Remaining paid time is not refunded. Operators can erase a person on request from the admin Users page (same rules, `DELETE /api/users/{id}`).

## Terms and Privacy

`/terms` and `/privacy` are public pages written to match what the product actually does (Paystack payments and saved cards, SociaVault data, the AI and publishing providers a customer connects, the one-trial rule, retention, cookies). The company name, contact email, address, governing law and refund wording are filled in by the operator under admin **Settings → Legal details** (`GET /api/public/legal`); until they are set the pages use neutral wording and the admin Overview shows a reminder. Sign-up links to both, and each new account records the Terms version it accepted (`users.terms_accepted_at`, `terms_version`; bump `legal.TERMS_VERSION` when the Terms change materially). **These are plain-language templates, not legal advice: have them reviewed for your business and country before taking real payments.**

## Subscriptions & billing

There is **no free plan**. A new workspace gets a **free trial** (7 days by default, with the limits of the trial plan), then must subscribe. Plans (price, interval, limits on accounts / team members / AI requests per month; `-1` = unlimited) are created in the admin console; Pro ($49.90 / month) and Agency ($99.90 / month) are seeded.

- **Off by default.** Until you tick *Enforce plan limits* in admin Settings nobody is limited or trialled and customers see no plans.
- **Trial:** length and the plan whose limits it uses are set in admin Settings (default 7 days, cheapest plan). The clock starts at the later of *workspace created* and *billing first switched on*, so enabling billing never expires anyone instantly. An operator can extend or end one workspace's trial from its page.
- **One trial per person, not per workspace.** The first workspace an email creates gets the trial; a second one starts expired ("already used its free trial") until it pays. Emails are normalised (case, `+tag`, Gmail dots) so `a+1@gmail.com` is the same person, and the claim survives deleting the workspace. Joining someone else's workspace by invite does not use up a trial. Workspaces that existed before this rule are grandfathered, and an operator can still give any workspace trial days. It does not stop a person using a genuinely different mailbox.
- **Disposable email addresses are refused** at sign-up, when inviting someone and when an invite creates an account (a built-in list of well-known throwaway providers, matched on the domain and its parent domains). In the admin console (Settings) you can add domains, allow-list a wrongly blocked one, test an address, or switch the check off. The list can never be complete; existing accounts are never affected.
- **After the trial (or a paid period) ends the workspace is expired:** it can still be **viewed**, but every change is refused with `402` (`code: trial_ended`) until it pays, scheduled SociaVault syncs for it stop (no provider credits spent) and queued posts wait. Paying (or an operator granting a plan) unlocks it at once. The app shows a trial bar with days left, and a red one once expired.
- Existing accounts, posts and members are never deleted; limits only stop **new** accounts, invites and AI calls.
- **Checkout, in the app:** Billing opens Paystack's popup in place (no redirect). The page then **polls** `GET /api/billing/payments/{reference}` every 3 s; the server asks Paystack for the result, so the browser's word is never trusted. A signed webhook (`POST /api/billing/webhook/paystack`, HMAC-SHA512) is a backup if the tab was closed. All paths are idempotent, and an underpaid amount or wrong currency is never granted.
- **Priced in USD, charged in local currency.** If your Paystack account cannot take USD (e.g. GHS-only), set the USD rate and *Charge customers in* = GHS in admin Settings: plans stay at $49.90 / $99.90, and checkout and renewals charge the converted amount at the current rate (shown to the customer as "≈ GHS 773.45 charged"). A renewal uses the rate on the day it is charged; if no rate is set, checkout is refused rather than charging the wrong amount.
- **Plan currency must match your Paystack account** (e.g. a GHS-only account cannot charge USD). Set it per plan in the admin console.
- **Auto-renew with a saved card.** At checkout the customer can tick *Renew automatically*. If they then pay by **card**, Paystack's reusable authorization is kept (encrypted; the browser only ever sees brand / last 4 / expiry) and a background job charges it **a day before the period ends**, so there is no gap and the new interval is added to the paid time. Consent is recorded per payment (`payments.save_card`). Mobile money and non-reusable cards cannot be saved.
  - **Retries:** a decline is retried about every 20 hours, up to 3 times; then auto-renew switches itself off, the reason is shown on Billing, and the customer is emailed (if email is set up). A charge that needs a PIN/OTP stays pending and is never repeated blindly.
  - **Never twice:** the job takes a database lease, claims each attempt in the database, and settles any unfinished charge with Paystack before it charges again, so a crash, a network error or two workers cannot double-charge.
  - **Controls:** customers can turn it off or remove the card on Billing; the operator can remove a card from the workspace page in the admin and run renewals on demand in Settings. An operator changing a workspace's plan manually also switches auto-renew off.
  - A renewal charges the plan's **current** price. Without a saved card each payment buys one interval and, when it ends, the workspace is expired (view-only) until it pays again.
- **Comp / manual grants:** set any plan on a workspace from the admin console, with an optional end date.
- **Metering:** AI requests are counted per workspace per month, only when the provider call succeeds.
- Set `HOOKTRACK_PUBLIC_URL` to your public address so Paystack's return link points at it. Paystack key: admin Settings (stored encrypted) or `PAYSTACK_SECRET_KEY`.

## The workflow: Hooks sync, decisions create the next work, Posts moves it to posted

1. **Accounts:** add each handle on its platform (TikTok, Instagram, YouTube). SociaVault pulls the posts and views.
2. **Hooks** is sync-only: it lists posted videos pulled from those accounts, with their decision (Scale / Tweak / Stop). There is no manual "new post" there.
3. **You decide what goes to Posts.** A Scale or Tweak hook has a **Post** column button on Hooks (and a **Post** button in its editor):
   - **Scale** sends a **Scripted** post (label S1, S2 …), linked to the winner, to film a matching video for.
   - **Tweak** sends an **Idea** post (T1, T2 …): the same hook to rework a little into a new script.
   - **Stop** has no next step. Pressing Post twice makes one post (`POST /api/posts/{id}/followup`); only hooks pulled in by the sync can be sent. Nothing is created automatically, and the editor of a synced hook has no Publish or schedule button: publishing and scheduling happen on a post under Posts.
4. **Posts** is the board: Idea → Scripted → Filmed → Posted. Only Idea and Scripted are filled for you (from Tweak and Scale decisions). **Filmed and Posted are moved by hand**: uploading a video, publishing and confirming a post never change its column. A Filmed post is published straight away or scheduled for later (📤 Publish). Posts pulled in by the sync live on Hooks, not on the board.

### Closing the loop: a card gets its real results

On a card you made (Idea / Scripted / Filmed / Posted) the editor has a **Posted video link** field. Paste the full address of the video once it is live. After the next sync (or straight away if the sync already has it) the card is linked to the synced video (`app/results.py`) and shows its **views, how it did against that account's normal, and the original hook's multiple** ("📊 79.9M views · 2.2× normal (original 2.2×)"). Linking never moves a card: Filmed, Posted and Analysed stay your own moves. Matched by the video's own id from TikTok, YouTube (watch, shorts, youtu.be) and Instagram (/p, /reel) addresses; short vm.tiktok.com links cannot be matched. On Hooks the Post column now shows the stage the card is in.

### Next actions, publish and target account

- **Dashboard → Next actions** (`GET /api/next-actions`): what needs you today, as links: Scale / Tweak hooks not sent yet (3+ days old), Filmed cards not published, Posted cards with no video link, Posted cards with results in, Idea / Scripted cards waiting 14+ days, and posted cards waiting for the next sync.
- **Publish** is refused for a card with no video file or link on TikTok / Instagram / YouTube ("Attach a video first"), and the button says so. When the provider's answer contains the posted address it is saved to the card's **Posted video link** automatically, so results match without pasting (the card still stays where you put it).
- **Send to Posts for another account:** with more than one account, **Post** asks which account the new video is for (default: the hook's own). A hook can be sent once per account (`POST /api/posts/{id}/followup?account_id=`).

### Verdict: did the made video beat the original?

When a Posted card is linked to its synced video and that video is 3+ days old, the card shows how it did against the hook it came from (**beat** it by 10%+, **matched** it, or **fell short** by 10%+, comparing × the account's normal) and a **Mark analysed** button. Marking it (`POST /api/posts/{id}/analyse`) moves it to Analysed and keeps the verdict. Hooks then shows "1 of 2 beat it" next to the hook, so you can see whether making matches of a winner pays off. Analysed is never filled automatically.

### Read on send, and Tweak variants

- **Read on send** (Settings → Decision rules, off by default): when you send a **Scale** hook to Posts, its video is read (about 1 SociaVault credit, once) so the card's Brief arrives with the first words and structure. If the read fails (no key, no public link) the card is still made and the toast says why. Send top 5 reads each one too (up to 5 credits).
- **Make 3 variants:** on a Tweak card still in Idea, the Brief has **Make 3 variants**: three Idea cards (a, b, c), each changing one thing (the first words, the first shot, the angle). Pressing it again does not make more (`POST /api/posts/{id}/variants`).

### Find my video (no link to paste)

A Filmed or Posted card has **🔎 Find my video** (on the board, and in the editor under the Posted video link). It lists the synced videos on that account posted since the card was made, best match first (same words, then newest), never the card's own origin and never one already matched to another card (`GET /api/posts/{id}/candidates`). **This one** links the card and brings its results back (`POST /api/posts/{id}/link`); the card is not moved. Pasting the address still works.

### Copies of your own copies

A synced video that is the result of a card you made from another hook shows **↻ made from “…” · generation 2** on Hooks (generation 3 is a copy of a copy). **Send top 5** and the dashboard's "not sent yet" count skip them, and pressing **Post** on one asks first, so a winner's own copy is only cloned again on purpose.

### A weekly pace and due dates

- **Weekly target** (Settings → Decision rules, 0 = off): the dashboard's Next actions card shows "This week: 3 of 5 filmed" with a bar. A card counts when it reaches Filmed this week (Monday to Sunday, workspace time), and stays counted when it moves on.
- **Due dates:** a card's date is its due date. A Scripted card sent from a hook is due in 3 days, an Idea in 7; change it in the editor. Cards show "due in 2d" or "overdue 3d" (red), and overdue cards appear in Next actions.

### Auto-sync and the credit meter

- **Auto-sync** was already there (a daily light sync of every account with Auto-sync on, run by the server; set `HOOKTRACK_SYNC_INTERVAL_HOURS=0` to turn it off). The dashboard's Next actions card now says whether it is on and when the accounts last synced.
- **Credit meter:** every SociaVault call that spends credits (syncs, video reads, comment reads) is added to a per-workspace ledger (`credit_usage`). The card shows "SociaVault credits this month: 42 (syncing 30 · video reads 8 · comments 4)" (`GET /api/credits`). Set **SociaVault credits per month** in Settings → Decision rules for a budget: the line turns red from 80%. The numbers are estimates from what each call reports, not your SociaVault invoice.

### Publishing through Late (Zernio)

Late's API wants each platform as `{platform, accountId}`. HookTrack lists the connected profiles itself and picks, per platform, the account whose username matches the card's account handle, else the first one (or set `account_ids` as `{"tiktok": "<id>"}` in the provider config). The video goes as `mediaItems` and posts with `publishNow`, or `scheduledFor` when scheduled. A platform that refuses comes back as a failed job with the platform's reason. Verified with a real test post on 2026-10-11 using an uploaded file delivered through a Cloudflare tunnel (`HOOKTRACK_PUBLIC_URL`).

### Publishing through HookTrack's own Late account

For customers who do not bring their own provider key. The platform admin sets **one** Late key (admin console → Settings → Publishing (Late), or `HOOKTRACK_LATE_API_KEY`).

- Each workspace gets its own Late **profile** (created on first use, `workspace_late`). **Settings → Publishing** lists the accounts connected under that profile and has **Connect TikTok / Instagram / YouTube** buttons that send the user to Late's sign-in; HookTrack never sees their password.
- A post goes **only** to accounts listed under the workspace's own profile. In this mode any `account_ids` or `base_url` a customer puts in the config is ignored, and an account is matched by the card's account handle, else the first of that platform.
- It is **on by default** for every workspace once the key exists. A workspace's own earlier provider key is kept but not used while the platform connection exists.
- **Not built yet:** disconnecting an account from inside HookTrack, a per-plan monthly post cap, and surfacing Late's own per-profile plan limit before it is hit (a full plan returns "no room for another customer profile").
- Late's API base is `https://zernio.com/api/v1` (override with `HOOKTRACK_LATE_BASE_URL`).

### Responsive layout

One set of header breakpoints (`static/app.css`, "responsive header"): above 1100 px brand, the six links and the tools share one row; from 1100 px down the links take their own full-width row of six equal cells; at 560 px and below they become two rows of three with larger tap targets, the tools fill the row above and the workspace picker spans the full width. Grid and flex children can shrink (`min-width: 0`), long words wrap, and media is capped to its container, so no page scrolls sideways at 320 px (checked on Dashboard, Accounts, Hooks, Posts, Calendar, Inspiration, Settings, Billing, Today and Views).

### Daily and weekly views fill themselves

- **Auto-sync is on by default** for new accounts, and was switched on for existing ones. The server's daily sync (about 1 credit per account) pulls posts and view totals.
- **Exact formula** (from the second day of syncing): an account's views for a day = the sum over its videos of (today's total − the previous snapshot). Refreshed on every sync, so *today so far* grows during the day. The week is the seven days added up.
- **First-week estimate** (`app/estimate.py`): until snapshots exist, each video posted in the last 7 days has its views so far spread over the days since it was posted with a typical short-form curve (35 / 25 / 15 / 10 / 7 / 5 / 3 %). Those cells are tagged **estimated** (italic, dashed), never overwrite anything you typed or read from a screenshot, and are replaced by the exact figure as soon as it exists. Views that older videos gained in those days are not known, so an estimate can only undercount.
- **Where each number came from** is shown on every cell: typed, read from a screenshot by AI, synced, or estimated. The dashboard marks the week with "~" when it includes estimates and shows what today has reached so far.
- **AI's part** stays reading a screenshot of the platform's own analytics (✨ AI entry), which gives true account-level totals, including views older videos gained. It is never used to guess a missing number.

### Inspiration in the flow

Inspiration is the second way in to Posts (the first is your own results on Hooks).

- **Use this** on a saved item makes an **Idea** card on Posts, due in 7 days, labelled `I1`, `I2`… The item's text is the starting hook and the card's notes say to keep the move and change the subject and words (`POST /api/inspiration/{id}/use`; with several accounts you pick which).
- **Attach inspiration to any card** (Idea / Scripted): the card's Brief gets an **Inspiration** section with the item's hook, your notes and its video player, and **+ Add inspiration** to attach more from the library (`POST /api/posts/{id}/inspiration`).
- **✨ Ideas** on the Posts page: AI suggests hooks from your best and worst posts and the items marked "Use in AI". Each suggestion has **Add to Posts as an Idea**; nothing is added until you press it.
- **Fill from link:** in Add inspiration, paste a TikTok, Instagram or YouTube link and press **Fill from link**: the caption, account, numbers, length and sound are read from it (about 1 credit, `POST /api/inspiration/lookup`; TikTok and YouTube shapes were checked live, Instagram is read defensively). It only fills empty fields.
- **Not built:** a competitor watchlist.

### Plan limits that follow the cost

Two limits sit beside accounts, members and AI calls (`plans.max_connected`, `plans.max_credits`; -1 = unlimited, edited in the admin console under Plans):

- **Connected publishing accounts.** Publishing costs per connected TikTok / Instagram / YouTube account, not per post. Pressing **Connect** past the plan's allowance is refused with a clear 402 ("Your Solo plan includes 1 connected publishing account. Upgrade under Billing to connect more").
- **Data credits per month.** Syncing, reading a video, reading comments and looking up a link all spend SociaVault credits (counted in `credit_usage`). At the allowance they are refused with a 402, a sync reports it per account, and the dashboard meter shows the use. Credits reset with the calendar month.
- **Add-ons:** `workspace_subscriptions.extra_connected` and `extra_credits` raise a workspace's limits above its plan (set with `PUT /api/workspaces/{id}/extras` in the admin console API until a self-serve purchase exists).
- **Seeded values:** Solo $19 (3 tracked, 1 connected, 150 credits), Pro 3 connected / 600 credits, Agency 10 connected / 4,000 credits. Prices can be changed in the admin console. Nothing is limited while billing is off.
- The free trial uses the cheapest plan's limits, so a trial can connect at most that plan's accounts and spend at most its credits.

### Pricing v2 (migration 0028)

| Plan | Price | Yearly (2 months free) | Tracked | Members | AI / month | Connected | Credits / month |
|---|---|---|---|---|---|---|---|
| Solo | $19 | $190 | 3 | 1 | 100 | 1 | 150 |
| Pro | $49 | $490 | 15 | 5 | 1,000 | 3 | 600 |
| Agency | $149 | $1,490 | 100 | 15 | 5,000 | 10 | 4,000 |

Why: tracking costs cents (a daily sync is about 30 credits, $0.002-0.005 each); a connected publishing account costs $3-6 a month at Late/Zernio. So the plans meter connected accounts and credits, tracked accounts are generous, and Agency is capped (a 100-account agency costs about $9-15 in data). Add-ons, set per workspace by the operator for now: extra connected account $8/month, extra 25 tracked accounts $10/month, 1,000 credits $8. Prices are edited in the admin console under Plans.

### Self-serve add-ons

On **Billing → Add-ons** a workspace admin buys extras on top of the plan with the same Paystack popup as a plan (`POST /api/billing/addons/checkout {kind, qty}`, polled through `GET /api/billing/payments/{reference}`):

| Add-on | Unit | Price | Lasts |
|---|---|---|---|
| `connected` | 1 connected publishing account | $8 | 30 days |
| `accounts` | 25 tracked accounts | $10 | 30 days |
| `credits` | 1,000 data credits | $8 | until the end of the month bought in |

- A paid add-on is a row in `addon_purchases` counted while `expires_at` is in the future, added to the plan limit (`billing.limit_for`) and to the operator's own `extra_*` grants. Retries and the webhook cannot apply one twice (unique reference).
- Prices come from `ADDONS` and can be overridden without a release with the `addon_prices` platform setting, e.g. `{"connected": 999}` (USD minor units).
- If an add-on lapses while more publishing accounts are connected than the plan allows, publishing is refused until it is renewed or an account is disconnected.
- **Auto-renewal:** connected-account and tracked-account add-ons (not credits) renew by themselves with the card saved for the plan. A purchase made while a card is saved (and "renew automatically" is on) is marked to renew; about a day before it ends the saved card is charged for the same quantity and the new period starts exactly where the old one ends (`addon_purchases.starts_at`), so the two never count together and nothing is charged twice (`ht_ar_<id>_…` references, an ambiguous network error is settled by verification). A declined card is retried every 20 hours up to 3 times, then renewal turns itself off and the customer is emailed; what was paid for keeps counting until it ends. Each add-on has a **Turn off auto-renew / Renew automatically** switch on Billing (`PATCH /api/billing/addons/{id}/auto-renew`); turning it on needs the plan's saved card.
- **Disconnect:** Settings → Publishing has a **Disconnect** button on each connected account (admins), with a warning when posts are scheduled or queued (`DELETE /api/publish/managed/accounts/{id}`). Only an account under the workspace's own Late profile can be removed. Late keeps a disconnected account listed for about an hour, so HookTrack hides it straight away and the slot is free at once; connecting that platform again clears the hide.

### The decision engine, v2

A video is compared with **its own account's normal** (median of that account's last 30 mature posts, at least 5; YouTube Shorts and long-form are separate). The call, from the measured multiple:

| Multiple of the account's normal | Call | Meaning |
|---|---|---|
| at least the winner threshold (default 2×), or a fast first day | **Scale** | repeat it |
| 1.2× up to the threshold | **Tweak** | it works: strengthen it |
| 0.7× to 1.2× | **Hold** | about normal, nothing to change |
| 0.5× to 0.7× | **Tweak** | below normal: rework it |
| 0.5× or less, once | **Tweak** | one weak post is not a pattern: rework the angle, not the account |
| 0.5× or less, and 2 of its last 3 posts were too | **Stop** | weak again and again |

- **Every measured call carries its sample size and a confidence** (low / medium / high) from the number of posts behind the normal and the video's age (settled at 8 days). The Hooks page shows "measured · medium confidence · may still move", and the basis line says "(12 posts of its own, medium confidence)".
- **Only Scale and Tweak make cards.** Hold and Stop do not.
- **Checking the calls:** every sync logs each young measured post's call per day (`decision_log`). Settings → Decision rules shows how many hooks called Scale around day 3 still beat the account's normal around day 30 (`GET /api/decisions/calibration`), with the same split by confidence. It needs about a month of syncing before it has anything to say.
- Thresholds (winner multiple, hook-score cut-offs) stay editable in Settings → Decision rules; the 0.7× and 1.2× Hold band edges are constants in `app/logic.py` (`HOLD_LOW`, `HOLD_HIGH`).

### Follower normalization and the Hold band

- **Hold band edges are settings now** (Settings → Decision rules: *Hold starts at* default 70%, *Hold ends at* default 120%). They are checked: starts between 50 and 99, ends from 100 up to below the winner threshold.
- **Per-follower view (secondary, never changes the call).** From the account's follower snapshots (weekly), each video's views are divided by the audience at the time it was posted and compared with the account's own median of that. It appears once the audience has been measured on two different days (before that it would only repeat the main number) and is shown when it disagrees with the raw reading by a clear margin: *"Part of this is audience growth: 2.4x in views, 1.3x relative to the audience at the time"* or *"Better than it looks"*. In the Hooks table it is a small "per follower 1.3×" line under the multiple, and in the hook's editor under "Audience size".

### The trial does not publish

While a workspace is only on its free trial (billing on, nothing paid, not complimentary) **connecting a publishing account and publishing are held**: each connected account costs real money, and a trial is for trying the tracking, decisions, cards and briefs. Settings → Publishing says "Publishing starts when you choose a plan" and disables the Connect buttons; a card's Publish button is disabled with the same reason; the API refuses with a 402 (connect) or 400 (publish). A paid or complimentary plan, or billing being off, lifts it at once (`billing.publishing_held`, `GET /api/addons/publish` → `held`).


## Backups and the restore drill

The app backs itself up and proves the backup works (`app/backup.py`; no `pg_dump` needed). Every 24 hours it writes
`backups/hooktrack-<UTC time>.tar.gz` (one COPY file per table plus a manifest of row counts and checksums, taken in a
single consistent snapshot) and keeps the newest 14. Once a week it restores the newest one into a throwaway database,
compares every table with the manifest, and drops it. Both results show under admin Settings → Database backups, and a
failure emails the operator.

| Variable | Default | Meaning |
|---|---|---|
| `HOOKTRACK_BACKUP` | `1` | `0` turns the schedule off |
| `HOOKTRACK_BACKUP_DIR` | `./backups` | where archives go |
| `HOOKTRACK_BACKUP_COPY_TO` | none | a second folder (mounted or synced, i.e. off this machine) each archive is also copied to |
| `HOOKTRACK_BACKUP_KEEP` | `14` | archives kept |
| `HOOKTRACK_BACKUP_HOURS` / `_DRILL_DAYS` | `24` / `7` | backup and restore-test intervals |

`python -m app.backup create | list | drill [archive] | restore <archive> <target-url>`. `restore` refuses the live
database unless `--allow-live`. The drill needs a database user that can `CREATE DATABASE`.

A backup holds customer data and password hashes, plus API keys encrypted with `HOOKTRACK_SECRET`. Keep the archives
private, and store the secret separately: without it a restore works but every saved key has to be re-entered. A backup
in the same folder as the database is not a disaster plan: set `HOOKTRACK_BACKUP_COPY_TO`.
