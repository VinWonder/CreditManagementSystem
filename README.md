# Duka Credit

Duka Credit is a Flask web app for small retailers who sell goods on credit.
It keeps customer accounts, credit sales, payments, and outstanding balances
in one place, with optional SMS and WhatsApp reminders.

The app uses Bootstrap and vanilla JavaScript. Credit sales can be queued in
the browser with IndexedDB when the connection is unavailable. SQLite is the
default for local development; PostgreSQL is supported for deployments.

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

## 2. Set up the project

Open the project folder in VS Code and install the Microsoft Python extension
if it isn't already installed.

Create and activate a virtual environment from the VS Code terminal:

```bash
python -m venv venv
```

Activate it:
- **Windows (PowerShell):** `venv\Scripts\Activate.ps1`
- **macOS / Linux:** `source venv/bin/activate`

Install the project dependencies:
```bash
pip install -r requirements.txt
```

Copy the example configuration and set a private `SECRET_KEY` in `.env`:
```bash
copy .env.example .env      # Windows
cp .env.example .env        # macOS / Linux
```
To send live SMS, choose a provider and add its credentials. Without provider
credentials, SMS-dependent flows use simulated messages in development; codes
are written to the Flask terminal.

### Optional: load demo data

This resets and repopulates the configured database, so only run it against a
local development database:
```bash
python seed.py
```

The demo account is:
- **Phone:** `+254700111222`
- **Password:** `password123`

The script also adds five customers, sample credit sales and payments, and
three reminder rules (7, 14, and 30 days overdue).

### Run the app
```bash
python run.py
```
Open **http://127.0.0.1:5000** in your browser.

### Try offline sales

Log in and open **New Credit Sale**. In your browser's developer tools, set
the Network connection to offline and submit a sale. It should be saved in
IndexedDB and synced when the connection comes back.

---

## 3. Features

| Feature | Where to find it |
|---|---|
| Retailer accounts and password hashing | `app/auth` and `Retailer` in `app/models.py` |
| Customer records | `app/customers` |
| Credit sales and payments | `app/transactions` and `app/payments` |
| Balance updates and receivables report | `Ledger.recalculate()` and `app/ledger` |
| Reminder rules and delivery | `app/reminders` |
| Offline sale queue and sync | `app/static/js/offline-db.js`, `app/static/js/sync.js`, and `/transactions/api/sync` |

Phone verification and SMS sign-in codes are currently disabled. Password
reset codes are supported.

---

## 4. PostgreSQL and deployment

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

For production, configure `APP_ENV=production`, a unique
`SECRET_KEY`, and `SESSION_COOKIE_SECURE=true` behind HTTPS. Keep
`AUTO_CREATE_DB=false`; production refuses SQLite and schema changes must be
applied with `flask db upgrade` before starting the new application version.
To create a migration after a schema change, run
`flask db migrate -m "describe the schema change"`, review the generated file,
then apply it with `flask db upgrade`.

For deployment:

- Use a managed PostgreSQL instance and set `DATABASE_URL` in the host's
  environment.
- Run the web workers with `SCHEDULER_ENABLED=false`. Start one separate
  `scheduler_worker.py` process with `SCHEDULER_ENABLED=true` to avoid
  duplicate reminders. Use the same database URL and secrets for both.
- Serve the app with Gunicorn rather than `python run.py`:
  ```bash
  gunicorn -w 4 -b 0.0.0.0:8000 run:app
  ```
- Terminate HTTPS at your proxy or hosting platform, and configure SMS or
   WhatsApp credentials in the host environment as needed.
- Never commit `.env` or put production credentials in source control.

---

## 5. Demo login

Available after running `python seed.py` against a local development database:

| Field | Value |
|---|---|
| Phone | `+254700111222` |
| Password | `password123` |
