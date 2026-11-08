"""Printable reports and analytics.

Shopkeepers get the reports built from what they can already see in the app
(sales, payments, balances and customers). Shop admins get every report,
including approvals, staff activity, balance corrections and reminders.
"""

import csv
import io
from datetime import datetime
from decimal import Decimal

from flask import Blueprint, Response, abort, render_template, request
from flask_login import current_user, login_required

from app.models import Customer
from app.reports.analytics import (
    PERIOD_CHOICES,
    REPORTS_BY_KEY,
    build,
    read_period,
    reports_for,
    summary_report,
)
from app.timezones import format_nairobi_datetime

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


@reports_bp.route("/")
@login_required
def index():
    period = read_period(request.args)
    overview = summary_report(current_user, period)
    return render_template(
        "reports/index.html",
        reports=reports_for(current_user),
        period=period,
        period_choices=PERIOD_CHOICES,
        overview=overview,
    )


@reports_bp.route("/<key>")
@login_required
def view(key):
    report_type = REPORTS_BY_KEY.get(key)
    if report_type is None:
        abort(404)
    if report_type.admin_only and not current_user.is_admin:
        abort(403)

    period = read_period(request.args)
    customers, customer = [], None
    if report_type.needs_customer:
        customers = (
            Customer.query.filter_by(retailer_id=current_user.retailer_id)
            .order_by(Customer.full_name.asc())
            .all()
        )
        customer_id = request.args.get("customer_id", type=int)
        if customer_id:
            customer = Customer.query.filter_by(
                retailer_id=current_user.retailer_id, customer_id=customer_id
            ).first_or_404()

    report = build(report_type, current_user, period, customer)
    if request.args.get("format") == "csv":
        return _csv(report, period)
    return render_template(
        "reports/view.html",
        report=report,
        report_type=report_type,
        period=period,
        period_choices=PERIOD_CHOICES,
        customers=customers,
        customer=customer,
        generated_at=datetime.utcnow(),
    )


def _cell(value, kind):
    if value is None:
        return ""
    if kind == "money" and isinstance(value, (int, float, Decimal)):
        return f"{Decimal(value):.2f}"
    if kind == "datetime" and isinstance(value, datetime):
        return format_nairobi_datetime(value, "%Y-%m-%d %H:%M")
    if kind == "date" and isinstance(value, datetime):
        return format_nairobi_datetime(value, "%Y-%m-%d")
    if kind == "pct" and isinstance(value, (int, float)):
        return f"{value:.1f}"
    return str(value)


def _csv(report, period):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([report.title])
    writer.writerow([current_user.business_name, report.subtitle])
    writer.writerow([])
    for metric in report.metrics:
        writer.writerow([metric.label, _cell(metric.value, metric.kind)])
    for table in report.tables:
        writer.writerow([])
        writer.writerow([table.title])
        writer.writerow([column.label for column in table.columns])
        for row in table.rows:
            writer.writerow([_cell(value, column.kind) for value, column in zip(row, table.columns)])
        if table.totals:
            writer.writerow([_cell(value, column.kind) for value, column in zip(table.totals, table.columns)])
    filename = f"duka-{report.key}-{period.start.isoformat()}-to-{period.end.isoformat()}.csv"
    # A byte-order mark so Excel opens the file as UTF-8.
    return Response(
        "﻿" + buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
