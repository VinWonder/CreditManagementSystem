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
| Admin and shopkeeper accounts, roles, and password hashing | `app/auth` and `Retailer` / `Shopkeeper` in `app/models.py` |
| Customer records and SMS/WhatsApp reminder preferences | `app/customers` |
| Credit sales and payments | `app/transactions` and `app/payments` |
| Balance updates and receivables report | `Ledger.recalculate()` and `app/ledger` |
| Reminder rules and delivery | `app/reminders` |
| Offline sale queue and sync | `app/static/js/offline-db.js`, `app/static/js/sync.js`, and `/transactions/api/sync` |

Retailer owners are shop admins. Admins create and deactivate shopkeeper
accounts from **Shopkeepers**; shopkeepers can add customers, record sales,
submit payments for admin approval, and view balances. Customer
edits/deletion, sending reminders, reminder rule changes, payment approvals,
and balance corrections are admin-only. A shopkeeper payment does not reduce
the customer's outstanding balance until approved. Partial approved payments
reduce the balance; a zero balance is shown as cleared. Manual corrections
require a reason and are retained in the customer balance audit history.

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
6. To inspect a stored password hash in pgAdmin, connect to `duka_credit`, open
   **Query Tool**, and run:
   ```sql
   SELECT retailer_id, business_name, password_hash
   FROM public.retailers;
   ```
   Each row is a retailer account. The `password_hash` value is not the
   original password and cannot be reversed to reveal it. Treat hashes as
   sensitive and do not share or publish query results. No rows means there
   are no retailer accounts in this database yet.
7. Start the app and create a retailer account. PostgreSQL starts empty; your
   existing SQLite database remains unchanged. Back it up before planning any
   data transfer.

For production, configure `APP_ENV=production`, a unique
`SECRET_KEY`, and `SESSION_COOKIE_SECURE=true` behind HTTPS. Keep
`AUTO_CREATE_DB=false`; production refuses SQLite and schema changes must be
applied with `flask db upgrade` before starting the new application version.
To create a migration after a schema change, run
`flask db migrate -m "describe the schema change"`, review the generated file,
then apply it with `flask db upgrade`.

### Configure and test SMS/WhatsApp reminders

Each retailer connects their own SMS and WhatsApp provider accounts from the
admin-only **Message providers** page in the application. Credentials are
encrypted before being stored in the database and are never returned by the
provider status API or rendered back into the page. Before deployment, set a
stable encryption key in the web and scheduler environments:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Store the generated value as `MESSAGING_CREDENTIALS_ENCRYPTION_KEY` in your
deployment secret manager (or a local, untracked `.env` file). Back it up
securely and keep it unchanged: existing provider credentials cannot be
decrypted if the key is lost or rotated. Install dependencies and apply
database migrations with `flask --app run db upgrade` before using the new
settings page.

For each retailer, choose Africa's Talking or Twilio and enter that account's
SMS credentials and sender. Add the retailer's Meta WhatsApp Phone Number ID
and access token to enable WhatsApp. Customers can choose SMS, WhatsApp, or
both, and reminder rules can target either channel. The account owner is the
only role allowed to manage provider credentials; shopkeepers can send through
their retailer's configured accounts.

Account verification SMS uses the application's existing `SMS_PROVIDER` and
SMS environment settings; reminder messages use the individual retailer
credentials saved in **Message providers**.

Meta may require an approved message template for business-initiated
WhatsApp messages outside the 24-hour customer-service window. This
integration currently sends text messages, so use SMS for scheduled reminders
outside that window. Recipients must opt in.

Reminder messages include the recorded credit-sale item descriptions and
Nairobi sale dates. Existing rule templates receive those details
automatically; new templates can place `{item_details}` where they belong.
`{customer_shop}` is available when a customer has an optional shop/business
name. Failed attempts show a sanitized provider reason in Dispatch history;
check the failed entry there first, then verify credentials, sender approval,
recipient opt-in/allow-list, and (for WhatsApp) template approval.

Sign in and check provider readiness with:

```javascript
fetch("/reminders/api/providers/status")
  .then(response => response.json())
  .then(console.log);
```

For a **live test**, use a customer with a valid international phone number,
an outstanding balance, an active rule whose overdue threshold has been met,
and an SMS/WhatsApp preference that includes the channel being tested. From
that customer's portfolio page, get the CSRF token and explicitly confirm the
send:

```javascript
const csrf = document.querySelector('input[name="csrf_token"]').value;
fetch("/reminders/api/customers/123/test", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-CSRFToken": csrf
  },
  body: JSON.stringify({ channel: "sms", confirm: true })
}).then(async response => ({
  status: response.status,
  result: await response.json()
})).then(console.log);
```

Replace `123` with the customer ID and `sms` with `whatsapp` to test that
channel. This endpoint sends the **actual configured reminder message** to
that customer and logs the matching rule; it is not a sandbox simulator.
Obtain customer consent first. Provider use may incur charges. A `202`
response means the provider accepted the request, not that the handset
received it; final delivery status requires provider status callbacks, which
are not currently configured. Without credentials, live sends are rejected
instead of being marked as successful. Automated tests mock provider calls
and never send real messages.

For deployment:

- Use a managed PostgreSQL instance and set `DATABASE_URL` in the host's
  environment.
- Run the web app against a managed PostgreSQL instance and set `DATABASE_URL`
  in the host's environment. There is no background worker to start: reminders
  are sent by the retailer from their own phone.
- Serve the app with Gunicorn rather than `python run.py`:
  ```bash
  gunicorn -w 4 -b 0.0.0.0:8000 run:app
  ```
- Terminate HTTPS at your proxy or hosting platform. Retailers send reminders
  from the phone number on their own shop account, so no provider credentials
  are entered in the app.
- Never commit `.env` or put production credentials in source control.

---

## 5. Demo login

Available after running `python seed.py` against a local development database:

| Field | Value |
|---|---|
| Phone | `+254700111222` |
| Password | `password123` |
