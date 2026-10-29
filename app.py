"""Smart Vending Jobs — deployable job board.

Stack: Flask + SQLite (stdlib) + Stripe Checkout (optional, env-gated).
No build step. The frontend is served from templates/index.html.

How posting works
-----------------
1. Employer submits the form and picks Standard ($99) or Featured ($199).
2. The job is saved as a DRAFT.
3. If STRIPE_SECRET_KEY is set, a Stripe Checkout Session is created and the
   employer is redirected to Stripe. Nothing goes live until Stripe confirms
   payment via the /api/webhook endpoint (event: checkout.session.completed),
   or via the success-page verification in /api/checkout/success.
4. If no Stripe key is set, the app runs in DEMO mode: the post is published
   immediately and no card is charged. Demo mode is shown clearly in the UI
   and in /api/config so it can never be mistaken for a live payment.

Environment variables
---------------------
STRIPE_SECRET_KEY      Secret key from the Stripe Dashboard (test or live).
STRIPE_WEBHOOK_SECRET  Signing secret for the /api/webhook endpoint.
DATABASE_PATH          Optional. Defaults to data/jobs.db next to app.py.
PORT                   Optional. Defaults to 5000.
"""
import json
import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, abort, g, jsonify, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DATABASE_PATH", BASE_DIR / "data" / "jobs.db"))
SEED_PATH = BASE_DIR / "jobs.json"

PRICES = {"standard": 9900, "featured": 19900}  # cents
TIER_LABELS = {"standard": "Standard", "featured": "Featured"}

app = Flask(__name__, static_folder="static", template_folder="templates")


def stripe_configured():
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def get_db():
    if "db" not in g:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            title TEXT NOT NULL,
            location TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT '',
            pay TEXT NOT NULL DEFAULT '',
            apply_url TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            featured INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'live',
            tier TEXT NOT NULL DEFAULT 'standard',
            stripe_session_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    db.commit()
    count = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    if count == 0 and SEED_PATH.exists():
        seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        for job in seed:
            db.execute(
                """INSERT INTO jobs
                   (company, title, location, state, pay, apply_url,
                    description, featured, status, tier)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'live', 'standard')""",
                (
                    job["company"], job["title"], job["location"],
                    job.get("state", ""), job.get("pay", ""),
                    job["apply_url"], job.get("description", ""),
                    1 if job.get("featured") else 0,
                ),
            )
        db.commit()


def row_to_job(row):
    return {
        "id": row["id"],
        "company": row["company"],
        "title": row["title"],
        "location": row["location"],
        "state": row["state"],
        "pay": row["pay"],
        "apply_url": row["apply_url"],
        "description": row["description"],
        "featured": bool(row["featured"]),
        "tier": row["tier"],
    }


def valid_apply_url(url):
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


@app.before_request
def _init():
    init_db()


@app.route("/")
def index():
    return send_from_directory(BASE_DIR / "templates", "index.html")


@app.route("/api/config")
def config():
    return jsonify({
        "stripe_configured": stripe_configured(),
        "demo_mode": not stripe_configured(),
        "standard_price_cents": PRICES["standard"],
        "featured_price_cents": PRICES["featured"],
    })


@app.route("/api/jobs")
def list_jobs():
    q = (request.args.get("q") or "").strip().lower()
    state = (request.args.get("state") or "").strip().upper()
    company = (request.args.get("company") or "").strip().lower()
    db = get_db()
    rows = db.execute(
        "SELECT * FROM jobs WHERE status = 'live' "
        "ORDER BY featured DESC, id ASC"
    ).fetchall()
    jobs = [row_to_job(r) for r in rows]
    if q:
        jobs = [j for j in jobs if q in f"{j['company']} {j['title']} {j['location']}".lower()]
    if state:
        jobs = [j for j in jobs if j["state"] == state]
    if company:
        jobs = [j for j in jobs if j["company"].lower() == company]
    companies = sorted({r["company"] for r in db.execute("SELECT company FROM jobs WHERE status='live'").fetchall()})
    states = sorted({r["state"] for r in db.execute("SELECT state FROM jobs WHERE status='live' AND state != ''").fetchall()})
    return jsonify({"jobs": jobs, "companies": companies, "states": states})


@app.route("/api/jobs/<int:job_id>")
def get_job(job_id):
    row = get_db().execute(
        "SELECT * FROM jobs WHERE id = ? AND status = 'live'", (job_id,)
    ).fetchone()
    if row is None:
        abort(404)
    return jsonify(row_to_job(row))


@app.route("/api/jobs", methods=["POST"])
def create_job():
    data = request.get_json(silent=True) or {}
    company = (data.get("company") or "").strip()
    title = (data.get("title") or "").strip()
    location = (data.get("location") or "").strip()
    apply_url = (data.get("apply_url") or "").strip()
    pay = (data.get("pay") or "").strip()
    description = (data.get("description") or "").strip()
    tier = (data.get("tier") or "standard").strip().lower()
    if tier not in PRICES:
        tier = "standard"
    if not company or not title or not location or not apply_url:
        return jsonify({"error": "Company, job title, location, and application link are required."}), 400
    if not valid_apply_url(apply_url):
        return jsonify({"error": "Application link must be a valid http(s) URL."}), 400
    if len(company) > 200 or len(title) > 200 or len(location) > 200:
        return jsonify({"error": "Company, title, and location must each be under 200 characters."}), 400
    state_match = re.findall(r",\s*([A-Z]{2})\b", location)
    state = state_match[-1] if state_match else ""
    if not description:
        description = "See the employer's application page for the full description and requirements."
    db = get_db()
    cur = db.execute(
        """INSERT INTO jobs
           (company, title, location, state, pay, apply_url, description,
            featured, status, tier)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)""",
        (company, title, location, state, pay, apply_url, description,
         1 if tier == "featured" else 0, tier),
    )
    job_id = cur.lastrowid
    db.commit()

    if not stripe_configured():
        # Demo mode: publish immediately, charge nothing. Never claim a charge happened.
        db.execute("UPDATE jobs SET status = 'live' WHERE id = ?", (job_id,))
        db.commit()
        return jsonify({"job_id": job_id, "demo_mode": True, "published": True}), 201

    import stripe
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    base = request.host_url.rstrip("/")
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": PRICES[tier],
                "product_data": {
                    "name": f"Smart Vending Jobs — {TIER_LABELS[tier]} job posting",
                    "description": f"30-day {TIER_LABELS[tier].lower()} posting for: {title} at {company}",
                },
            },
            "quantity": 1,
        }],
        success_url=f"{base}/?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base}/?checkout=cancelled&job_id={job_id}",
        metadata={"job_id": str(job_id), "tier": tier},
    )
    db.execute(
        "UPDATE jobs SET stripe_session_id = ? WHERE id = ?",
        (session.id, job_id),
    )
    db.commit()
    return jsonify({"job_id": job_id, "demo_mode": False, "checkout_url": session.url}), 201


@app.route("/api/checkout/success")
def checkout_success():
    session_id = request.args.get("session_id") or ""
    if not session_id or not stripe_configured():
        return jsonify({"published": False, "error": "No Stripe session to verify."}), 400
    import stripe
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception as exc:  # noqa: BLE001 - surface Stripe's own error text
        return jsonify({"published": False, "error": str(exc)}), 400
    if session.payment_status != "paid":
        return jsonify({"published": False, "payment_status": session.payment_status}), 200
    db = get_db()
    db.execute(
        "UPDATE jobs SET status = 'live' WHERE stripe_session_id = ?",
        (session_id,),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM jobs WHERE stripe_session_id = ?", (session_id,)
    ).fetchone()
    return jsonify({"published": True, "job": row_to_job(row) if row else None})


@app.route("/api/webhook", methods=["POST"])
def stripe_webhook():
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not webhook_secret:
        return jsonify({"error": "Webhook secret is not configured."}), 400
    import stripe
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    payload = request.get_data(as_text=True)
    sig = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
    except Exception as exc:  # noqa: BLE001 - Stripe verification failure
        return jsonify({"error": str(exc)}), 400
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        if session.get("payment_status") == "paid":
            db = get_db()
            db.execute(
                "UPDATE jobs SET status = 'live' WHERE stripe_session_id = ?",
                (session["id"],),
            )
            db.commit()
    return jsonify({"received": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
