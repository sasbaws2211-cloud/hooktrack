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

## AI configuration
**Settings -> AI** (admins) picks a provider per workspace: **Anthropic**, **OpenAI**, or any **OpenAI-compatible** endpoint
(Groq, OpenRouter, Together, Mistral, vLLM, LiteLLM, Ollama ...). Key and model are stored per workspace (key encrypted, never returned),
with a Test connection button. Server-wide defaults: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` (never used for custom endpoints).
- Changing provider or endpoint drops the saved key, so a key is never sent to a different destination.
- Custom endpoints are validated and re-checked on every call; private/loopback/link-local addresses are blocked (SSRF).
  On a trusted single-tenant host set `HOOKTRACK_ALLOW_PRIVATE_AI_URLS=1` to allow e.g. `http://localhost:11434/v1`.
- Screenshot reading needs a vision-capable model; every feature needs tool/JSON-output support (a JSON-in-text fallback is built in).

## What it does
- **Daily views**: account x day grid, type `85k` or paste/drop screenshots for AI entry (you review before saving).
- **Calendar**: planned / posted / idea posts; AI plans a week from your winning hooks.
- **Hooks**: log each post, VSC score (manual or AI), SCALE 24+ / MUTATE 21-23 / KILL <21.
- **Dashboard**: weekly totals, 8-week trend, by phone, what to scale/kill, and "ask your data".
- **Rules** (Accounts tab) make the spreadsheet's thresholds configurable.
- **Import** the original .xlsx (idempotent): `python -m app.import_xlsx <workspace_id> file.xlsx 2026-10-05` (or use the Accounts page).

## Tests
`python -m pytest tests` (needs the Postgres container; uses a separate `hooktrack_test` database; AI calls are mocked).

## Not built (on purpose)
No TikTok API pull, no publishing to platforms, no email sending (invites are copy-paste links), no password reset or workspace deletion yet.
