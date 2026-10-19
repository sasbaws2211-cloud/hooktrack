# Deploying HookTrack on Render

HookTrack = 2 web services (customer app + platform admin) + 1 Postgres. `render.yaml` defines all three.

## 0. Before you start
- The git repo root is `Desktop/hooktrack` and the app lives in its `hooktrack/` subfolder; `render.yaml` already sets `rootDir: hooktrack`.
- Put `render.yaml` at the **repo root** (move/copy it up one level) or point Render at it in the Blueprint settings.
- `.env` is git-ignored: never commit it. Production values go in the Render dashboard.
- Push to GitHub/GitLab: `git add -A && git commit -m "render deploy" && git push`.

## 1. Create the Blueprint
Render dashboard -> **New -> Blueprint** -> pick the repo -> Render reads `render.yaml` and proposes
`hooktrack` (web), `hooktrack-admin` (web), `hooktrack-db` (Postgres). Fill the prompted env vars (step 3), then **Apply**.

Cost note: both web services are `starter` (always on). Do **not** use the free web tier: it sleeps, which stops the
sync/digest/backup/publish loops that run inside the app process. Do not use free Postgres (deleted after 30 days).

## 2. Env vars: what each is
| Var | Service | Value |
|---|---|---|
| `DATABASE_URL` | both | auto-linked to hooktrack-db |
| `HOOKTRACK_SECRET` | both, **identical** | auto-generated on `hooktrack`; copy it into `hooktrack-admin`. **Back it up**: losing it makes stored API keys undecryptable |
| `HOOKTRACK_SECURE_COOKIES` | both | `1` |
| `HOOKTRACK_TRUSTED_PROXIES` | both | `1` (Render has one proxy; makes per-IP signup limits work) |
| `HOOKTRACK_PUBLIC_URL` | both | e.g. `https://hooktrack.onrender.com` or your custom domain (used in email/invite/report links) |
| `HOOKTRACK_ALLOWED_ORIGINS` | hooktrack | extra trusted hosts, e.g. your custom domain |
| `HOOKTRACK_ADMIN_EMAIL` / `HOOKTRACK_ADMIN_PASSWORD` | admin | first platform admin (10+ char password); only used while none exists. Remove the password var afterwards |
| `HOOKTRACK_OPS_EMAIL` | hooktrack | where balance/backup alerts go |
| `HOOKTRACK_SOCIAVAULT_API_KEY` | hooktrack | view sync (your balance was ~15 credits, top up first) |
| `ANTHROPIC_API_KEY`, `HOOKTRACK_MODEL` | hooktrack | optional server-wide AI default |
| `PAYSTACK_SECRET_KEY` | hooktrack | billing; use `sk_live_...` only after testing with `sk_test_...` |
| `HOOKTRACK_LATE_API_KEY` | hooktrack | publishing via Late/Zernio (optional) |
| `RESEND_API_KEY` | hooktrack | Resend API key (`re_...`). **Setting it turns on mandatory email verification**, digests and alerts |
| `HOOKTRACK_EMAIL_FROM` | hooktrack | e.g. `HookTrack <hello@yourdomain.com>`; the domain must be verified in Resend (DNS records), otherwise it only delivers to your own Resend account email |
| `HOOKTRACK_BACKUP_DIR` | hooktrack | `/var/data/backups` (on the persistent disk) |
| `HOOKTRACK_BACKUP_COPY_TO` | hooktrack | optional off-machine copy path |

The app creates/migrates its schema on boot; no manual migration step.

## 3. Persistent disk
`render.yaml` attaches a 5 GB disk at `/var/data` to `hooktrack` (disks require a paid instance and limit the service to one instance).
Uploads are stored under the app folder (`hooktrack/uploads`), which is ephemeral on Render: if you rely on uploaded media, either set it up to
write under `/var/data` or accept losing it on redeploy. Backups use `HOOKTRACK_BACKUP_DIR` so they persist. Also enable Render's own
Postgres backups (paid plans include them).

## 4. Webhooks and domains
- **Paystack**: dashboard -> Settings -> API & Webhooks -> webhook URL `https://<your-domain>/api/billing/webhook/paystack` and the callback URL to your domain.
- Custom domain: service -> Settings -> Custom Domains, add the CNAME at your DNS; TLS is automatic. Then update `HOOKTRACK_PUBLIC_URL`.
- Keep the **admin** service private if you can: add an IP allow-list (Render Pro) or leave its URL unshared; it has its own login and cookie.

## 5. First run checklist
1. Open `https://<hooktrack>.onrender.com`, create a workspace (first signup adopts any pre-existing data).
2. Open the admin URL, log in with `HOOKTRACK_ADMIN_EMAIL/PASSWORD`, set plans, SociaVault, and Paystack in Settings.
3. Admin -> Settings: run "Run sync now" and confirm the SociaVault balance card shows a number.
4. Remove `HOOKTRACK_ADMIN_PASSWORD` from the admin service env.
5. Check Logs for `SociaVault scheduler on` and no tracebacks.

## 6. Moving local data (optional)
```bash
docker exec hooktrack-pg pg_dump -U hooktrack -Fc hooktrack > hooktrack.dump
pg_restore --no-owner --clean -d "<Render External Database URL>" hooktrack.dump
```
If you restore local data, set Render's `HOOKTRACK_SECRET` to your local one (the contents of `.secret`) so saved keys still decrypt.

## 7. Updating
Push to the connected branch; Render auto-deploys. Schema upgrades run at startup.
