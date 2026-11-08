from flask import Blueprint, current_app, jsonify, make_response, render_template, send_from_directory, url_for
from flask_login import login_required, current_user
from sqlalchemy import func

from app import listing
from app.extensions import db
from app.models import Customer, CreditTransaction, Payment, Ledger
from app.payments.forms import PaymentDecisionForm

main_bp = Blueprint("main", __name__)

# Keep the dashboard short on phones; 'View all' opens the full history.
RECENT_ACTIVITY_LIMIT = 2


@main_bp.route("/")
def index():
    return render_template("main/index.html")


@main_bp.route("/manifest.webmanifest")
def manifest():
    """Lets phones install Duka Credit to the home screen like an app."""
    response = jsonify(
        name="Duka Credit",
        short_name="Duka Credit",
        description="Track credit sales, payments and reminders for your shop.",
        id="/",
        start_url=url_for("main.dashboard"),
        scope="/",
        display="standalone",
        orientation="portrait",
        background_color="#f4f7f6",
        theme_color="#173c35",
        # Lets the app ask Chrome whether it was installed in full or only as a shortcut.
        related_applications=[{"platform": "webapp", "url": url_for("main.manifest")}],
        icons=[
            {"src": url_for("static", filename="icons/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": url_for("static", filename="icons/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": url_for("static", filename="icons/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    )
    response.mimetype = "application/manifest+json"
    return response


@main_bp.route("/service-worker.js")
def service_worker():
    """Served from the site root so it can cover every page of the app."""
    response = make_response(
        send_from_directory(current_app.static_folder, "js/service-worker.js", mimetype="text/javascript")
    )
    # Always check for a new version so updates reach installed apps quickly.
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@main_bp.route("/offline")
def offline():
    return render_template("main/offline.html")


@main_bp.route("/features")
def features():
    return render_template("main/features.html")


@main_bp.route("/dashboard")
@login_required
def dashboard():
    retailer_id = current_user.retailer_id

    total_customers = Customer.query.filter_by(retailer_id=retailer_id).count()

    total_outstanding = (
        db.session.query(func.coalesce(func.sum(Ledger.outstanding_balance), 0))
        .filter(Ledger.retailer_id == retailer_id)
        .scalar()
    )

    overdue_customers = (
        Ledger.query.filter(
            Ledger.retailer_id == retailer_id,
            Ledger.outstanding_balance > 0,
            Ledger.days_overdue > 0,
        ).count()
    )

    recent_transactions = (
        CreditTransaction.query.filter_by(retailer_id=retailer_id)
        .order_by(CreditTransaction.transaction_date.desc())
        .limit(RECENT_ACTIVITY_LIMIT)
        .all()
    )

    recent_payments = (
        Payment.query.filter_by(retailer_id=retailer_id, approval_status="approved")
        .order_by(Payment.payment_date.desc())
        .limit(RECENT_ACTIVITY_LIMIT)
        .all()
    )

    # Shopkeeper payments the retailer must verify, newest first, with a form per
    # row so the retailer can approve without leaving the dashboard.
    pending_payments = []
    if current_user.is_admin:
        pending_payments = (
            Payment.query.filter_by(retailer_id=retailer_id, approval_status="pending")
            .order_by(Payment.payment_date.asc())
            .limit(5)
            .all()
        )

    # Ageing buckets for the chart
    buckets = {"0-7": 0, "8-14": 0, "15-30": 0, "31-60": 0, "61+": 0}
    ledgers = Ledger.query.filter(
        Ledger.retailer_id == retailer_id, Ledger.outstanding_balance > 0
    ).all()
    for l in ledgers:
        d = l.days_overdue
        amt = float(l.outstanding_balance)
        if d <= 7:
            buckets["0-7"] += amt
        elif d <= 14:
            buckets["8-14"] += amt
        elif d <= 30:
            buckets["15-30"] += amt
        elif d <= 60:
            buckets["31-60"] += amt
        else:
            buckets["61+"] += amt

    # Widgets: this week's credit given vs collected, this month's totals and
    # the customers who owe the most.
    shop_sales = CreditTransaction.query.filter_by(retailer_id=retailer_id)
    shop_collections = Payment.query.filter_by(retailer_id=retailer_id, approval_status="approved")
    week_given = listing.pulse(shop_sales, CreditTransaction.transaction_date, CreditTransaction.amount, days=7)
    week_collected = listing.pulse(shop_collections, Payment.payment_date, Payment.amount_paid, days=7)
    week_peak = max([bar["total"] for bar in week_given + week_collected] + [0])
    week = [
        {
            "weekday": given["weekday"],
            "date": given["date"],
            "given": given["total"],
            "collected": collected["total"],
            "given_height": int(given["total"] * 100 / week_peak) if week_peak else 0,
            "collected_height": int(collected["total"] * 100 / week_peak) if week_peak else 0,
        }
        for given, collected in zip(week_given, week_collected)
    ]
    month_start = listing.day_start_utc(listing.nairobi_today().replace(day=1))
    month_given = (
        db.session.query(func.coalesce(func.sum(CreditTransaction.amount), 0))
        .filter(CreditTransaction.retailer_id == retailer_id, CreditTransaction.transaction_date >= month_start)
        .scalar()
    )
    month_collected = (
        db.session.query(func.coalesce(func.sum(Payment.amount_paid), 0))
        .filter(
            Payment.retailer_id == retailer_id,
            Payment.approval_status == "approved",
            Payment.payment_date >= month_start,
        )
        .scalar()
    )
    owing_count = len(ledgers)
    top_debtors = sorted(ledgers, key=lambda ledger: ledger.outstanding_balance, reverse=True)[:8]
    bucket_total = sum(buckets.values())
    bucket_shares = {
        band: (amount * 100 / bucket_total if bucket_total else 0) for band, amount in buckets.items()
    }

    return render_template(
        "main/dashboard.html",
        week=week,
        week_given_total=sum((bar["given"] for bar in week), 0),
        week_collected_total=sum((bar["collected"] for bar in week), 0),
        month_given=month_given,
        month_collected=month_collected,
        owing_count=owing_count,
        top_debtors=top_debtors,
        bucket_shares=bucket_shares,
        total_customers=total_customers,
        total_outstanding=total_outstanding,
        overdue_customers=overdue_customers,
        recent_transactions=recent_transactions,
        recent_payments=recent_payments,
        pending_payments=pending_payments,
        payment_decision_forms={
            payment.payment_id: PaymentDecisionForm() for payment in pending_payments
        },
        buckets=buckets,
    )
