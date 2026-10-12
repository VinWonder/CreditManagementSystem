# Duka Credit — Web-Based Credit Purchases Management System

A working prototype of the system described in *"A Web-Based Credit Purchases
Management System with Automated Payment Reminders for Small-Scale
Retailers"* (Ochieng, 2026). Built with **Python (Flask)**, **Bootstrap 5**,
**vanilla JavaScript + IndexedDB** (offline-first), and a **SQL database**
(SQLite for development, PostgreSQL-ready for production).

It implements every actor/use case from your use case diagram (Retailer,
Customer via SMS/WhatsApp, System Administrator, SMS Gateway, WhatsApp
Business API) and every table from your ERD/database schema (`retailers`,
`customers`, `credit_transactions`, `payments`, `ledgers`, `reminder_rules`,
`message_dispatch_log`).

---

## 1. What's included

```
credit_system/
├── app/
│   ├── __init__.py           # Application factory
│   ├── extensions.py         # db, login_manager, migrate, scheduler
│   ├── models.py             # Retailer, Customer, CreditTransaction, Payment,
│   │                         # Ledger, ReminderRule, MessageDispatchLog
│   ├── auth/                 # Register / Login / Logout
│   ├── main/                 # Landing page + Dashboard
│   ├── customers/            # Customer CRUD
│   ├── transactions/         # Record credit sales + offline sync API
│   ├── payments/             # Log payments
│   ├── ledger/                # Receivables / ageing report
│   ├── reminders/            # Reminder rules, ageing engine, SMS/WhatsApp dispatch
│   ├── templates/            # Bootstrap 5 Jinja templates
│   └── static/
│       ├── css/style.css
│       └── js/
│           ├── offline-db.js  # IndexedDB cache for offline credit sales
│           └── sync.js        # Auto-sync when connection returns
├── config.py
├── run.py                    # App entry point
├── seed.py                   # Demo data generator
├── requirements.txt
├── .env.example
└── .gitignore
```

## 2. Step-by-step setup in VS Code

### Step 1 — Open the project
1. Unzip the downloaded folder.
2. Open VS Code → `File > Open Folder...` → select `credit_system`.
3. Install the **Python** extension (Microsoft) if you don't have it.

### Step 2 — Create a virtual environment
Open a VS Code terminal (`` Ctrl+` ``) and run:

```bash
python -m venv venv
```

Activate it:
- **Windows (PowerShell):** `venv\Scripts\Activate.ps1`
- **macOS / Linux:** `source venv/bin/activate`

VS Code should prompt "Select this environment for the workspace" — click **Yes**.

### Step 3 — Install dependencies
```bash
pip install -r requirements.txt
```

### Step 4 — Configure environment variables
```bash
copy .env.example .env      # Windows
cp .env.example .env        # macOS / Linux
```
Open `.env` and set a real `SECRET_KEY`. Set `SMS_PROVIDER` and configure
either Africa's Talking or Twilio credentials to send live phone-verification,
sign-in, password-reset, and reminder SMS. With no SMS credentials in
development, codes are simulated and written to the Flask terminal.

### Step 5 — Seed demo data (optional but recommended)
```bash
python seed.py
```
This creates a demo retailer you can log in with immediately:
- **Phone:** `+254700111222`
- **Password:** `password123`

It also creates 5 customers with realistic transaction/payment history and
3 pre-configured reminder rules (7, 14, 30 days overdue).

### Step 6 — Run the app
```bash
python run.py
```
Open **http://127.0.0.1:5000** in your browser. Use the `F5` "Run and
Debug" panel in VS Code (with a Python launch config pointing at `run.py`)
if you want breakpoints.

### Step 7 — Try the offline feature
1. Log in, go to **New Credit Sale**.
2. Open DevTools (`F12`) → Network tab → set throttling to **Offline**.
3. Submit a sale — it's cached in IndexedDB and a yellow "saved offline"
   notice appears.
4. Set Network back to **Online** — the badge in the navbar shows "Syncing…"
   and the sale is pushed to the server automatically.

---

## 3. Core features mapped to your project objectives

| Objective / Use Case | Implementation |
|---|---|
| Register / Log In | `app/auth` — Flask-Login, hashed passwords |
| Phone verification / sign-in MFA | International phone input and one-time SMS code required after each valid password |
| Manage Customer Profiles | `app/customers` — full CRUD |
| Record Credit Transactions | `app/transactions` — updates ledger automatically |
| Log Payments | `app/payments` — updates ledger automatically |
| Update Ledger Balance | `Ledger.recalculate()` in `app/models.py` |
| View Receivables Reports | `app/ledger` — sortable ageing report |
| Run Ageing Analysis | `app/reminders/engine.py` |
| Configure Reminder Rules | `app/reminders` — trigger day, channel, message template |
| Schedule Automated Reminders | APScheduler daily cron job in `scheduler_jobs.py` |
| Dispatch SMS/WhatsApp Message | `app/reminders/dispatch_service.py` (Africa's Talking + Meta Cloud API) |
| Sync Offline Data (IndexedDB) | `static/js/offline-db.js` + `static/js/sync.js` + `/transactions/api/sync` |

---

## 4. Moving to production

### Connect a local PostgreSQL database

1. Open pgAdmin and connect to your local PostgreSQL server.
2. Create a login role for the application (do not use the `postgres` superuser
   for the app): open **Query Tool** on the maintenance database and run:
   ```sql
   CREATE ROLE duka_app LOGIN PASSWORD 'replace-with-a-strong-password';
   CREATE DATABASE duka_credit OWNER duka_app;
   ```
   Use a unique password and keep it private.
3. In `.env`, keep `APP_ENV=development` for local HTTP testing, set
   `AUTO_CREATE_DB=false`, and set the database URL:
   ```env
   DATABASE_URL=postgresql://duka_app:URL_ENCODED_PASSWORD@localhost:5432/duka_credit
   ```
   URL-encode special characters in the password. Never share or commit `.env`.
4. Install dependencies and apply the tracked schema:
   ```powershell
   .\venv\Scripts\python.exe -m pip install -r requirements.txt
   .\venv\Scripts\python.exe -m flask --app run db upgrade
   ```
5. Verify the app's active database connection without printing the URL (it
   contains credentials):
   ```powershell
   .\venv\Scripts\python.exe -m flask --app run shell
   ```
   In the Python shell, run:
   ```python
   from app.extensions import db
   from sqlalchemy import text

   print(db.engine.url.drivername)
   print(db.session.execute(text("SELECT current_database()")).scalar())
   ```
   The driver should be `postgresql+psycopg`, and the database name should be
   the PostgreSQL database configured in `.env`.
6. Start the app and create a retailer account. PostgreSQL starts empty; your
   existing SQLite database remains unchanged. Back it up before planning any
   data transfer.

For a public production deployment, configure `APP_ENV=production`, a unique
`SECRET_KEY`, and `SESSION_COOKIE_SECURE=true` behind HTTPS. Keep
`AUTO_CREATE_DB=false`; production refuses SQLite and schema changes must be
applied with `flask db upgrade` before starting the new application version. To create future migrations, run
`flask db migrate -m "describe the schema change"`, review the generated file,
then apply it with `flask db upgrade`.

Run the web workers with `SCHEDULER_ENABLED=false`. Start exactly one separate
`scheduler_worker.py` process with `SCHEDULER_ENABLED=true` to avoid sending
duplicate reminders from multiple web workers. Use the same PostgreSQL URL and
secrets in both processes.

1. **Database:** switch `DATABASE_URL` in `.env` to a managed PostgreSQL
   instance (see §5 below) — no code changes needed, SQLAlchemy handles both.
2. **Migrations:** the initial Flask-Migrate repository is included. For each
   future schema change, generate, review, and apply a migration:
   ```bash
   flask db migrate -m "describe the schema change"
   flask db upgrade
   ```
3. **WSGI server:** don't use `python run.py` in production. Use:
   ```bash
   gunicorn -w 4 -b 0.0.0.0:8000 run:app
   ```
4. **HTTPS:** put it behind Nginx or a platform (Render/Railway/Fly.io) that
   terminates TLS for you.
5. **Secrets:** never commit `.env`. Set real environment variables on your
   host instead.
6. **Live SMS/WhatsApp:** fill in `AT_API_KEY` (Africa's Talking) and/or
   `WHATSAPP_TOKEN` + `WHATSAPP_PHONE_ID` (Meta Cloud API) in your production
   environment.

---

## 5. Recommended technology upgrades (beyond the MVP)

The current stack (Flask + Bootstrap + vanilla JS + SQLite/PostgreSQL) is a
deliberately lean, dependency-light choice that matches "responsive web
technologies" from your objectives and is realistic for a final-year
prototype. For a system heading toward real-world deployment with many
concurrent retailers, consider:

- **PostgreSQL over SQLite** — SQLite is great for local dev/demo, but
  concurrent writes from many retailers need a real client-server database.
  Free tiers: Supabase, Neon, Railway.
- **Celery + Redis instead of APScheduler** — APScheduler runs in-process,
  which is fine for one server. Celery + Redis (or Celery + RabbitMQ) scales
  reminder dispatch across multiple worker processes/servers and gives you
  retries, rate-limiting, and monitoring (Flower).
- **Progressive Web App (PWA)** — add a `manifest.json` and a Service Worker
  to cache the app shell itself (not just transaction data), so retailers
  can literally install "Duka Credit" as an app icon on their phone and open
  it with zero connectivity.
- **Twilio as an SMS/WhatsApp fallback** — Africa's Talking is cheaper and
  more reliable for Kenyan numbers, but Twilio's WhatsApp Business API is
  easier to sandbox-test if you want a second option.
- **Row-Level Security / multi-tenancy hardening** — if you move to
  PostgreSQL, enforce `retailer_id` isolation at the database level (RLS
  policies) in addition to the application-level filters already in the code.
- **Docker** — a `Dockerfile` + `docker-compose.yml` (app + Postgres + Redis)
  makes the whole stack reproducible for examiners/markers and for real
  deployment.
- **Testing** — add `pytest` + `pytest-flask` for the ledger-calculation
  logic in particular (`Ledger.recalculate()`), since correctness of the
  balance math is the single most important guarantee this system makes.
- **Observability** — Sentry (error tracking) and a simple `/health`
  endpoint, especially important for the "System uptime, cloud
  synchronisation logs, and API dispatch success rates" maintenance
  activities described in Chapter 3 of your proposal.

None of these are required to run or demo the current prototype — they're
listed here as the natural next steps your proposal's own "Final System
Deployment" and "Maintenance" phases point toward.

---

## 6. Default login (after seeding)

| Field | Value |
|---|---|
| Phone | `+254700111222` |
| Password | `password123` |
