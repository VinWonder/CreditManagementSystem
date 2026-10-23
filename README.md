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

### Use it on a phone or tablet

The app works in any modern browser on phones, tablets, laptops and desktops.
On phones and tablets the menu folds into the ☰ button and tables show one
card per row, so nothing runs off the side of the screen.

**On the same Wi-Fi as your computer (testing):**

1. Connect the phone to the same Wi-Fi network as the computer running the app.
2. Start the app with `python run.py`. It prints two addresses, for example:
   ```
   On this computer:  http://127.0.0.1:5000
   On your phone:     http://192.168.100.9:5000   (same Wi-Fi network)
   ```
3. Type the **On your phone** address into the phone's browser (Chrome,
   Safari, Samsung Internet...). Use `http://`, not `https://`. The address
   can change when the computer reconnects to Wi-Fi, so check what `run.py`
   prints each time.
4. If the page does not load, allow Python through Windows Firewall: the first
   time `run.py` starts, Windows asks; tick the network type you are on and
   choose **Allow access**. (Settings → Privacy & security → Windows Security →
   Firewall & network protection → Allow an app through firewall → Python.)
5. Optional: add it to the home screen (Chrome: ⋮ → **Add to Home screen**;
   Safari: Share → **Add to Home Screen**) so it opens like an app.

**Install it like an app:** on a phone the app offers itself for installing.
Android browsers (Chrome, Edge, Samsung Internet) show an *Install Duka Credit*
banner whose Install button opens the phone's own install prompt; iPhone
Safari shows the two taps instead (Share, then Add to Home Screen). Installed,
it opens full screen from its own home-screen icon. The banner only appears
on phones and tablets, never once installed, and "Not now" hides it for two
weeks; *Install app* is also in the account menu. Installing needs HTTPS, so it
works on Render but not over plain `http://` on your Wi-Fi. The service worker
stores no pages or customer data, only a "You're offline" page.

**From anywhere (mobile data, other networks):** the app has to be reachable
on the internet. For a quick test, run `ngrok http 5000` and open the
`https://…ngrok-free.app` address it shows on any phone. For real use, deploy
the app to a server with a domain and HTTPS (see below); that address works on
every device and is also what M-Pesa prompts need for instant confirmation.

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

A credit sale can hold several items bought at the same time (for example
Chips KES 100 and Soda KES 300): add a row per item and the total is worked
out. It stays one sale on one date. When logging a payment, tick the items it
is for (for example only the Soda); with nothing ticked, the payment pays the
oldest unpaid items first. The customer's page shows each item as paid, partly
paid or due, payment lists show what a payment was for, and one reminder lists
only what is still owed, item by item.

Each item row has a quantity and the price of one, so 3 sodas at KES 100 are
one line of KES 300 shown as "Soda ×3". When a ticked item was bought in a
number, the payment page asks how many the customer is paying for (for example
2 of the 3 sodas) and fills in the amount; the rest stays due, and the receipt
says "Soda ×2 of 3". This works for M-Pesa prompts too. Items saved before
quantities existed count as one of each, so old totals do not change.

After the admin logs or approves a payment (or an M-Pesa prompt is paid), a
receipt page offers a ready-written confirmation for the customer: the amount,
the date and time in EAT, how they paid and the reference, the items the
payment covered and the balance still owed. The admin can edit it, then press
WhatsApp or SMS to send it from their own phone. Any confirmed payment can be
receipted again from payment history.

Every payment records how the customer paid: cash, bank transfer/deposit,
agent withdrawal, M-Pesa Send Money, M-Pesa Till, M-Pesa Paybill, cheque, or
an M-Pesa prompt sent from the app (see below).
The M-Pesa transaction code is optional for M-Pesa and agent payments; cheques need
the cheque number, so the admin can check them before approving a
shopkeeper's submission; bank references are optional. The method and
reference appear in payment history, dashboard approvals, and the customer's
page. Payments recorded before this feature show "Not recorded".

Each shop name, phone number and email inbox can register only once. Shop
names are compared ignoring capitals and extra spaces; phone numbers in any
format, including numbers already used by a shopkeeper; and emails by the inbox
they reach, so `owner+2@gmail.com` or `o.w.n.e.r@gmail.com` cannot open a second
shop for `owner@gmail.com`. The database enforces the same rules. Passwords may
not contain the shop's phone number or email name. Logging out returns to the
home page.

A sign-up whose email stays unverified for 24 hours (`UNVERIFIED_SIGNUP_HOURS`)
is treated as abandoned: if someone registers with its shop name, phone number
or email, the abandoned sign-up is deleted so it cannot block the real owner.
Verified accounts and accounts with any customers, sales, payments or
shopkeepers are never removed. To clear all abandoned sign-ups at once:
`flask --app run accounts purge-unverified` (add `--dry-run` to list them first).

New retailers must verify the email they register with: a 6-digit code is
emailed at sign-up and must be entered before the first sign-in. Codes are
stored hashed, expire after 15 minutes, allow 5 attempts, and can be resent
once a minute. Accounts created before this feature are treated as verified.
Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` and `MAIL_FROM`
in `.env` to send real email (for Gmail, use an App Password). Without
`SMTP_HOST`, development prints the code in the Flask terminal.

Sessions end after 12 idle hours (`SESSION_IDLE_HOURS`). In development,
restarting the server also signs everyone out (`SESSION_ENDS_ON_RESTART`);
saving a code file and letting the auto-reloader restart does not.

Passwords must be 8-128 characters with an uppercase letter, a lowercase
letter, a number and a symbol, and no spaces. The forms show a live checklist.

Forgotten passwords: the retailer enters their phone number or email and a
6-digit reset code is emailed to the address on the account, usually within
seconds (accounts without an email get it by SMS). A new code can be requested
once a minute. Shops manage their name, logo, phone, email and password from Profile in the
account menu. Phone verification and SMS sign-in codes are currently disabled.

---

### Deploy on Render

1. **Database:** New + → PostgreSQL (region Frankfurt). Copy its *Internal
   Database URL*.
2. **Web service:** New + → Web Service from this repository, same region.
   - Build command: `pip install -r requirements.txt`
   - Start command:
     `flask --app run db upgrade && gunicorn run:app --workers 2 --timeout 60 --bind 0.0.0.0:$PORT`
3. **Environment:** `PYTHON_VERSION=3.12.8`, `APP_ENV=production`, `SECRET_KEY`
   (generate), `SESSION_COOKIE_SECURE=true`, `DATABASE_URL` (internal URL),
   `MESSAGING_CREDENTIALS_ENCRYPTION_KEY`, `PUBLIC_BASE_URL` (the service's
   `https://…onrender.com` address), `MAIL_FROM`, and email (below).
4. **Email:** Render's free plan blocks outgoing SMTP, so Gmail settings cannot
   send codes there. Create a free account at https://www.brevo.com, add and
   verify your `MAIL_FROM` address under *Senders*, create an API key under
   *SMTP & API → API keys*, and set `BREVO_API_KEY`. When it is set, emails go
   through Brevo instead of SMTP.

**Redeploy:** pushing to `main` redeploys automatically. To redeploy by hand,
open the service → *Manual Deploy* → *Deploy latest commit*. Database changes
run on start-up.

**Manage accounts on Render** from your own computer (free services have no
shell). Copy the database's *External Database URL*, then in PowerShell:

```powershell
$env:DATABASE_URL = "the External Database URL"
$env:MESSAGING_CREDENTIALS_ENCRYPTION_KEY = "the same key as on Render"
venv\Scripts\python.exe -m flask --app run accounts list
venv\Scripts\python.exe -m flask --app run accounts purge-unverified --all   # clear unverified sign-ups
venv\Scripts\python.exe -m flask --app run accounts delete <shop>            # delete a test shop
```

Close that PowerShell window afterwards so your local app uses your own
database again.

### Set up M-Pesa payment prompts (STK Push)

**For shops:** on **Log payment**, choose the customer, enter the amount, pick
**M-Pesa prompt (STK Push)** and confirm the customer's Safaricom number (it
fills in from the customer). The customer's phone shows a prompt to pay the
shop and they enter their M-Pesa PIN. A waiting page follows the result; when
M-Pesa confirms it, the payment is recorded as approved with the M-Pesa
receipt number. Shopkeepers can send prompts too: M-Pesa's confirmation is the
proof, so no admin approval is needed. Prompts take whole shillings, up to what
the customer owes.

**For the developer:** every shop is paid into **its own** Paybill or Till.
Shops never see M-Pesa setup; you connect each one from the server terminal.
The prompt option only appears for a shop once you have connected it, so a
prompt can never pay into another shop's number.

The text on the customer's phone comes from Safaricom, for example
*"Do you want to pay KES 500 to MAMA NJERI SHOP account no. MAMA NJERI S?"*:
the business name is the one registered on the shop's Paybill/Till, and the app
puts the shop name (first 12 letters) in the account field.

#### Onboard a shop

1. The shop needs its own Safaricom **Paybill** or **Till (Lipa Na M-Pesa)**
   number and a business administrator on the M-Pesa business (org) portal.
2. On https://developer.safaricom.co.ke, choose **Go Live** for that
   Paybill/Till with the **M-Pesa Express** API. The shop's administrator
   confirms it. Safaricom issues production consumer keys for the app and
   emails the production passkey.
3. Find the shop and connect it (you are asked for the key, secret and passkey
   without them being shown):
   ```powershell
   flask --app run mpesa list
   flask --app run mpesa set <shop> --environment production --type paybill --shortcode 123456
   flask --app run mpesa set <shop> --environment production --type till --shortcode <store-number> --till-number <till-number>
   ```
   `<shop>` is the retailer id from `list`, the shop's phone number or email.
   The keys are checked with Safaricom before they are saved, then stored
   encrypted with `MESSAGING_CREDENTIALS_ENCRYPTION_KEY` (back it up and never
   change it, or saved keys can no longer be read).
4. Send a KES 1 prompt from that shop's **Log payment** page to your own phone
   to confirm the passkey.

Manage shops later with:

```powershell
flask --app run mpesa test <shop>      # check the saved keys with Safaricom
flask --app run mpesa disable <shop>   # hide prompts, keep the keys
flask --app run mpesa enable <shop>
flask --app run mpesa remove <shop>    # delete the keys
```

Production needs the app on a public `https://` address with
`PUBLIC_BASE_URL` set to it, so Safaricom can report results instantly. Without
it (for example on your own computer) the waiting page asks Safaricom for the
result instead.

#### Try it in the sandbox

Use your sandbox app's consumer key and secret, the test Paybill **174379**,
and Safaricom's public sandbox passkey
`bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919`
(also on **APIs → M-Pesa Express → Simulate**):

```powershell
flask --app run mpesa set <shop> --environment sandbox --type paybill --shortcode 174379
```

A wrong passkey gives "Wrong credentials" when a prompt is sent. The sandbox
limits how often it can be called; if the app says Safaricom is busy, wait a
minute.

### Set up verification emails

Retailers only receive their sign-up code by email once SMTP is configured.
Until then the verify page warns that email is not set up and the code is
printed in the Flask terminal.

1. Use an email account for the shop (Gmail shown here).
2. Turn on 2-Step Verification: https://myaccount.google.com/security
3. Create an App Password: https://myaccount.google.com/apppasswords
4. In `.env`, fill in:
   ```env
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USE_TLS=true
   SMTP_USERNAME=yourshop@gmail.com
   SMTP_PASSWORD=the-16-character-app-password
   MAIL_FROM=Duka Credit <yourshop@gmail.com>
   ```
   For Outlook use `smtp-mail.outlook.com`; for other providers use their
   SMTP host and port (465 uses SSL automatically).
5. Check delivery:
   ```powershell
   .\venv\Scripts\python.exe -m flask --app run send-test-email you@example.com
   ```
   It prints `Sent.` or the exact reason it failed (for example a refused
   login, which usually means the normal password was used instead of an
   App Password).
6. Restart the app so it picks up the new settings.

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
| Password | `password123` (demo data only; new passwords must meet the rules above) |
