# Deploying FarmLink — a real, working runbook

Backend compute on **Render** (free), database on **Neon** (free, and unlike
Render's own free Postgres, doesn't expire). Frontend on **Vercel** (free).
Everything here reflects what was actually hit and fixed while deploying
this project, not a clean-room writeup — including two real failures.

## Why Render + Neon, not just Render for everything

Render's free web service has no expiration — it just spins down after
15 min idle (~1 min cold start on the next request). But Render's free
**Postgres** database expires 30 days after creation. Neon's free
Postgres is a standing offer with no expiration (0.5GB storage, 100
compute-hours/month, checked against Neon's docs). So: Render for
compute, Neon for the database. Nothing in this project cares which
Postgres it talks to — `db/session.py` just reads `DATABASE_URL` from
the environment.

---

## Step 0 — Push to GitHub

```bash
cd farmlink_deterministic
git init && git add . && git commit -m "FarmLink backend"
git remote add origin https://github.com/<you>/farmlink-backend.git
git push -u origin main
```

Same for the frontend, separately, when you get to Step 4.

## Step 1 — Create the Neon database first

(Before Render, so you have the connection string ready when Render
asks for it.)

1. `neon.tech` -> sign up (no credit card) -> **New Project**. Pick a
   region close to Singapore/Asia if offered, to match Render's region
   below and minimize cross-region latency.
2. On the project dashboard, find **Connection Details**. You'll see
   two connection strings:
   - **Pooled** (hostname contains `-pooler`) -- for high-concurrency
     serverless apps. **Don't use this one here.**
   - **Direct** (no `-pooler`) -- use this one, for both migrations and
     runtime. This project's connection pool tops out at 15 connections
     (SQLAlchemy defaults), far under Neon's 104-connection direct
     limit on the free tier, so the pooler adds complexity with no
     benefit for this project's scale.
3. Copy the **direct** string. It looks like:
postgresql://alex:AbC123dEf@ep-cool-darkness-123456.us-east-2.aws.neon.tech/neondb?sslmode=require
4. **Change the scheme** from `postgresql://` to `postgresql+psycopg2://`
   -- `db/session.py` expects the driver named explicitly. Keep
   `?sslmode=require`. You'll paste this into Render in Step 2.

## Step 2 — Backend on Render

1. `dashboard.render.com` -> **New +** -> **Blueprint** -> connect your
   `farmlink-backend` repo. It reads `render.yaml`.
2. Render will prompt you for `DATABASE_URL` (marked `sync: false` in
   the blueprint, since it's a secret) -- paste the Neon direct string
   from Step 1, with the scheme already changed.
3. Click **Apply**. Build runs `pip install -r requirements.txt &&
   alembic upgrade head` (verified in a clean venv this session).
4. Copy the **real** assigned URL from the dashboard (Render appends a
   random suffix, e.g. `farmlink-api-xxxx.onrender.com`).
5. Confirm: open `https://<your-url>/health` in a browser. Expect
   `{"status":"ok",...}`.

### If step 3 fails with "could not translate host name ... Name or service not known"

This is what happened the first time: `render.yaml` had the web
service pinned to `region: singapore` but the (then Render-managed)
database had no region set, so it silently defaulted elsewhere --
Render's private-network hostnames only resolve within the same
region. Now that the database is Neon (not Render-managed), this
specific failure shouldn't recur, but if you see this exact error
again, it means something is still resolving to a Render-internal
`dpg-xxxxx-a` hostname instead of Neon's public one -- double check
`DATABASE_URL` in Render's Environment tab actually holds the Neon
string, not a leftover Render Postgres reference.

## Step 3 — Seed the database (once, manually, from your own machine)

Render's **free tier has no Shell access** (that's a paid Starter-plan
feature -- confirmed by hitting this directly: the Shell tab shows an
upgrade prompt, not a terminal). So run the one-time seed script
locally instead, pointed at Neon:

```bash
cd farmlink_deterministic
python3 -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

export DATABASE_URL="postgresql+psycopg2://alex:AbC123dEf@ep-cool-darkness-123456.us-east-2.aws.neon.tech/neondb?sslmode=require"
# Windows PowerShell: $env:DATABASE_URL="postgresql+psycopg2://..."

python3 -m db.seed_from_json
```

Expect:
```json
{"market_prices": 672, "buyers": 100, "buyer_demands": 265, "buyers_source_file": "buyers_augmented.json", "logistics_providers": 15, "cold_storage_facilities": 10}
```

**Do not** run this a second time against the same database -- it's
not idempotent (throws `IntegrityError` on `buyer_profiles`' unique
constraint on a second run). This is also why it's a manual step here
and not in Render's build command: a build command re-runs on every
deploy, which would break every redeploy after the first.

Confirm real matching works, not just that the server is up:
```bash
curl -X POST https://<your-render-url>/agents/sell-decision \
  -H "Content-Type: application/json" \
  -d '{"crop":"Onion","district":"Kolhapur","quantity_quintals":20,"grade":"A","data_source_mode":"postgres"}'
```
Expect a real buyer shortlist with names, scores, and net-realization
figures -- not just a 200 status.

## Step 4 — Frontend on Vercel

1. `vercel.com` -> **Add New -> Project** -> import `farmlink-frontend`.
   Vercel auto-detects Next.js and, because `next.config.js` has
   `output: 'export'`, serves the static `out/` build with zero extra
   config.
2. **Before deploying**, add env var `NEXT_PUBLIC_API_BASE_URL` = your
   real Render URL from Step 2. This bakes in at build time (verified
   this session by building locally with a placeholder URL and
   confirming it appears literally in the compiled JS bundle) -- must
   be set *before* you click Deploy.
3. Deploy. Copy the assigned Vercel URL.

## Step 5 — Close the loop

Render -> `farmlink-api` -> **Environment** -> set:
CORS_ALLOWED_ORIGINS=https://<your-vercel-url>

Save (Render redeploys automatically). Without this, every request
from the deployed frontend fails CORS before reaching any endpoint.

## Step 6 — Verify the real, deployed, end-to-end flow

Open the Vercel URL, walk Farmer -> guest login -> My Lots -> New Lot ->
Find buyers (try Onion/Kolhapur). Should hit your live Render backend
and Neon database and show real matches -- the same flow verified
locally in a real browser earlier, now on the public internet.

---

## Capacitor / Android build (separate from the web deploy above)

```bash
echo "NEXT_PUBLIC_API_BASE_URL=https://<your-render-url>" > .env.production.local
npm run build
npx cap init "FarmLink" "in.farmlink.app" --web-dir=out
npx cap add android
npx cap sync
npx cap open android
```

Not run in any session so far (`npx cap add android` needs the Android
SDK) -- flagging honestly rather than claiming it's verified.