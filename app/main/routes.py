from flask import Blueprint, current_app, jsonify, make_response, render_template, send_from_directory, url_for
from flask_login import login_required, current_user
from sqlalchemy import func

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

    return render_template(
        "main/dashboard.html",
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
