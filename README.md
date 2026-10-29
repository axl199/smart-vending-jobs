# Smart Vending Jobs — deployable version

A public job board for smart vending, micro-market, and unattended retail
field technicians. Searchable homepage, per-job detail pages with an
employer application link, and an employer Post a Job flow.

Pricing: **$99 Standard** (30-day listing) / **$199 Featured** (30-day listing,
pinned to the top of search results).

Seeded with 20 listings from `jobs.json` (Canteen/Compass, Aramark
Refreshments, Sodexo/InReach, Imperial) on first run. Seeded job descriptions
are generic role overviews — the employer's application page is the source
of truth for the full description, and the site says so.

## Minimal-involvement deploy (do this, in this order)

This folder is already git-ready and host-ready. The only steps that
genuinely need the owner are the ones that need the owner's accounts/keys —
no keys are invented or stored here.

1. Push this folder to a git repo you own (or use Render Blueprint below).
2. Render: create a new service from that repo — `render.yaml` in this
   folder already sets the build and start commands. Railway: create a new
   service from the repo — `railway.toml` and the Procfile already set the
   start command.
3. In the host dashboard only, set `STRIPE_SECRET_KEY` and
   `STRIPE_WEBHOOK_SECRET` (test keys first), and point a Stripe webhook at
   `https://YOUR-DOMAIN/api/webhook` for `checkout.session.completed`.
4. Test with a Stripe test card, then switch to live keys.

That is the whole owner checklist. Everything else — code, seed data,
pricing ($99/$199), payment gating, featured pinning — is already done.

## Run locally (demo mode, no Stripe)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
# open http://127.0.0.1:5000
```

With no `STRIPE_SECRET_KEY` set, the app runs in demo mode: employer posts
publish immediately and no card is charged. The demo banner says so in the UI.

## Go live with Stripe

1. In the Stripe Dashboard, get a secret key. Start in **test mode** first.
2. Set these environment variables on your host (Render, Railway, Fly, etc. —
   never put keys in chat, git, or this README):
   - `STRIPE_SECRET_KEY`
   - `STRIPE_WEBHOOK_SECRET` — from a webhook endpoint pointing at
     `https://YOUR-DOMAIN/api/webhook`, listening for
     `checkout.session.completed`.
3. Deploy, then test end-to-end in Stripe test mode: post a job, pay with a
   test card, confirm the job goes live and the Featured job pins to the top.
4. Only then switch the keys to live mode.

How payment gating works: a submitted job is saved as a draft. With Stripe
configured, the employer is redirected to Stripe Checkout. The job goes live
only after Stripe confirms payment — via the webhook, or via the success-page
check at `/api/checkout/success`. Featured jobs sort first in search results.

## Deploy (example: Render or Railway)

- Push this folder to a git repo.
- Render: New Web Service, build `pip install -r requirements.txt`,
  start `gunicorn app:app --bind 0.0.0.0:$PORT` (the included Procfile does
  this on Railway-style hosts).
- Set the Stripe environment variables above in the host dashboard.
- Note on persistence: the default database is SQLite in `data/jobs.db`.
  That is fine for a demo / small launch, but on hosts with ephemeral disks
  a redeploy can wipe it. For a production launch, point `DATABASE_PATH` at
  persistent storage or migrate the `jobs` table to Postgres (the SQL is
  deliberately simple and portable).

## What is NOT in this version

- No employer accounts or login — payment is the gate, by design.
- No admin moderation queue — a paid post publishes automatically after
  payment. Add moderation (status `pending_review` instead of `live`) if
  spam becomes a problem.
- No live Stripe keys are included, and no real charge can happen until you
  add your own keys in your host's dashboard.
