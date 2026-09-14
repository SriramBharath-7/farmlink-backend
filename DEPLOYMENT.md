# Deploying FarmLink — a real, working runbook

This gets the backend (`api_v2.py` + Postgres) and frontend live on the
public internet, using free tiers: **Render** for the backend + database,
**Vercel** for the frontend. Everything in this file was checked against
each platform's current (Sept 2026) documented behavior before writing
it down — not assumed from training data — and the parts that could be
tested without your own hosting accounts (requirements.txt sufficiency,
`$PORT` binding, migration idempotency, static-export env-var baking)
were actually run in this session, not just described.

**What I could not do myself**: actually create the Render/Vercel/GitHub
accounts and click deploy — that needs your credentials, not mine. This
file gets you to a copy-paste-ready state and tells you exactly what to
click.

---

## Read this before you start: the one real trade-off

Render's **free** Postgres database expires 30 days after creation (14-day
grace period to upgrade before deletion), and Render's **free** web
service spins down after 15 minutes idle (about a 1-minute cold start on
the next request). Fine for a hackathon demo happening within that
window — just don't let it be a surprise mid-judging. Two ways to avoid
the cold-start surprise on demo day:
- Hit `/health` a couple of minutes before you go live to warm it up, or
- Upgrade the web service to Render's paid Starter tier (~$7/mo) for the
  week of the demo.

If your judging date is more than ~30 days out, swap Postgres providers:
**Neon** (neon.tech) has a permanent free tier. Nothing in this project
cares which Postgres you use — `DATABASE_URL` is just a connection
string (`db/session.py` reads it from the environment either way), so
this is a one-line swap, not a rewrite.

---

## Step 0 — Push to GitHub

Both Render and Vercel deploy from a git repo. From each project folder:

```bash
cd farmlink_deterministic
git init
git add .
git commit -m "FarmLink backend: deterministic engines + Postgres + api_v2"
# create an empty repo on github.com first, then:
git remote add origin https://github.com/<you>/farmlink-backend.git
git push -u origin main
```

```bash
cd farmlink-frontend
git init
git add .
git commit -m "FarmLink frontend"
git remote add origin https://github.com/<you>/farmlink-frontend.git
git push -u origin main
```

`.gitignore` files are already in both folders (added this session) so
`venv/`, `node_modules/`, `.env.local`, and build output never get
committed.

---

## Step 1 — Backend on Render

1. Go to `dashboard.render.com` → **New +** → **Blueprint**.
2. Connect the `farmlink-backend` repo. Render reads `render.yaml`
   (already in this folder) and shows you two resources: a free
   Postgres database (`farmlink-db`) and a free web service
   (`farmlink-api`). Click **Apply**.
3. Render provisions the database, then builds the web service —
   `pip install -r requirements.txt && alembic upgrade head` (verified
   in this session: this exact requirements.txt is sufficient — a
   clean venv installed from it alone passed all 128 tests — and
   `alembic upgrade head` is a safe no-op if already current).
4. Once deployed, note the web service's real URL from the Render
   dashboard (something like `https://farmlink-api-xxxx.onrender.com` —
   Render appends a suffix if the plain name is taken, so use the
   *actual* URL shown, not the one guessed in `render.yaml`'s comments).
5. **Confirm it's alive**: `curl https://<your-render-url>/health` should
   return `{"status":"ok",...}`.

## Step 2 — Seed the database (once, manually)

Render's dashboard → your `farmlink-api` service → **Shell** tab (or
`render.yaml`'s equivalent manual-job feature), run:

```bash
python3 -m db.seed_from_json
```

**Do not** put this in the build command — verified in this session that
`seed_from_json.run()` is not idempotent (a second run throws a
`UniqueViolation` on `buyer_profiles`), so it would break every future
redeploy if it ran automatically on each build. Run it once here; if you
ever need to reset the data, drop and recreate the Render database first.

Confirm it worked:
```bash
curl -X POST https://<your-render-url>/agents/sell-decision \
  -H "Content-Type: application/json" \
  -d '{"crop":"Onion","district":"Kolhapur","quantity_quintals":20,"grade":"A","data_source_mode":"postgres"}'
```
You should get back a real buyer shortlist (same shape verified against
`direct` mode in this session for this exact crop/district combo).

## Step 3 — Frontend on Vercel

1. Go to `vercel.com` → **Add New** → **Project** → import the
   `farmlink-frontend` repo. Vercel auto-detects Next.js and, because
   `next.config.js` has `output: 'export'`, serves the static `out/`
   build with zero extra config (confirmed current Vercel behavior).
2. Before the first deploy, add an environment variable in Vercel's
   project settings:
   - `NEXT_PUBLIC_API_BASE_URL` = your real Render URL from Step 1
     (e.g. `https://farmlink-api-xxxx.onrender.com`)
   - This is **baked in at build time** (verified in this session — built
     locally with a placeholder Render URL and confirmed it appears
     literally in the compiled JS bundle), so it must be set *before*
     you click Deploy, not after.
3. Deploy. Note the assigned Vercel URL (e.g.
   `https://farmlink-frontend.vercel.app`).

## Step 4 — Close the loop: tell the backend about the frontend's real URL

Back in Render → `farmlink-api` → **Environment**, set:
```
CORS_ALLOWED_ORIGINS=https://farmlink-frontend.vercel.app
```
Save (Render redeploys automatically). Without this, every request
from the deployed frontend fails with a CORS error before it reaches
any endpoint — `api_v2.py`'s CORS middleware only allows origins in
this list (see its comments), and its dev-mode defaults don't include
your Vercel URL.

## Step 5 — Verify the real, deployed, end-to-end flow

Open the Vercel URL in a browser, pick Farmer → guest login → My Lots →
New Lot → Find buyers, using Onion/Kolhapur or one of the other
known-good demo combos. This should hit your live Render backend and
show real matches — the same flow verified locally in a real browser
this session, now on the public internet instead of localhost.

---

## Capacitor / Android build (separate from the web deploy above)

The Android app is a *different* build of the same frontend — a native
wrapper around a static bundle, not something that talks to
`localhost` on the phone. Before building it:

```bash
# .env.local (or .env.production.local) — must point at the DEPLOYED backend,
# not localhost, since the phone has no idea what "localhost" means on your laptop
echo "NEXT_PUBLIC_API_BASE_URL=https://<your-render-url>" > .env.production.local

npm run build              # produces out/ with the real backend URL baked in
npx cap init "FarmLink" "in.farmlink.app" --web-dir=out
npx cap add android
npx cap sync
npx cap open android        # opens Android Studio to build the APK
```

This part hasn't been run in this session (`npx cap add android` needs
the Android SDK, which isn't in this sandbox) — flagging that honestly
rather than claiming it's verified.
