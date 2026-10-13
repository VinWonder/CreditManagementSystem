from datetime import datetime
from decimal import Decimal
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db


class Retailer(UserMixin, db.Model):
    """A shop owner / kiosk operator. Acts as the authenticated user (tenant)."""

    __tablename__ = "retailers"

    retailer_id = db.Column(db.Integer, primary_key=True)
    business_name = db.Column(db.String(100), nullable=False)
    owner_name = db.Column(db.String(100), nullable=False)
    phone_number = db.Column(db.String(20), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=True, index=True)
    phone_verified_at = db.Column(db.DateTime, nullable=True)
    logo_filename = db.Column(db.String(255), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    customers = db.relationship(
        "Customer", backref="retailer", lazy="dynamic", cascade="all, delete-orphan"
    )
    transactions = db.relationship(
        "CreditTransaction", backref="retailer", lazy="dynamic", cascade="all, delete-orphan"
    )
    payments = db.relationship(
        "Payment", backref="retailer", lazy="dynamic", cascade="all, delete-orphan"
    )
    reminder_rules = db.relationship(
        "ReminderRule", backref="retailer", lazy="dynamic", cascade="all, delete-orphan"
    )
    ledgers = db.relationship(
        "Ledger", backref="retailer", lazy="dynamic", cascade="all, delete-orphan"
    )

    # Flask-Login expects get_id() -> str; UserMixin provides it using self.id,
    # so we alias the primary key.
    def get_id(self):
        return str(self.retailer_id)

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def __repr__(self):
        return f"<Retailer {self.business_name}>"


class PasswordResetCode(db.Model):
    """A short-lived, one-time verification code for password recovery."""

    __tablename__ = "password_reset_codes"

    reset_id = db.Column(db.Integer, primary_key=True)
    retailer_id = db.Column(db.Integer, db.ForeignKey("retailers.retailer_id"), nullable=False)
    code_hash = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    verified_at = db.Column(db.DateTime, nullable=True)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    retailer = db.relationship(
        "Retailer",
        backref=db.backref(
            "password_reset_codes",
            lazy=True,
            cascade="all, delete-orphan",
        ),
    )


class PhoneVerificationCode(db.Model):
    """A short-lived, one-time code used to verify a retailer phone."""

    __tablename__ = "phone_verification_codes"

    verification_id = db.Column(db.Integer, primary_key=True)
    retailer_id = db.Column(db.Integer, db.ForeignKey("retailers.retailer_id"), nullable=False)
    code_hash = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    verified_at = db.Column(db.DateTime, nullable=True)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    retailer = db.relationship(
        "Retailer",
        backref=db.backref(
            "phone_verification_codes",
            lazy=True,
            cascade="all, delete-orphan",
        ),
    )


class LoginChallenge(db.Model):
    """One-time SMS second factor issued after valid retailer credentials."""

    __tablename__ = "login_challenges"

    challenge_id = db.Column(db.Integer, primary_key=True)
    retailer_id = db.Column(db.Integer, db.ForeignKey("retailers.retailer_id"), nullable=False)
    code_hash = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    retailer = db.relationship(
        "Retailer",
        backref=db.backref("login_challenges", lazy=True, cascade="all, delete-orphan"),
    )


class Customer(db.Model):
    """A retailer's credit customer ('mteja')."""

    __tablename__ = "customers"

    customer_id = db.Column(db.Integer, primary_key=True)
    retailer_id = db.Column(db.Integer, db.ForeignKey("retailers.retailer_id"), nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    phone_number = db.Column(db.String(20), nullable=False, index=True)
    preferred_channel = db.Column(db.String(10), default="sms")  # sms | whatsapp | both
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    transactions = db.relationship(
        "CreditTransaction", backref="customer", lazy="dynamic", cascade="all, delete-orphan"
    )
    payments = db.relationship(
        "Payment", backref="customer", lazy="dynamic", cascade="all, delete-orphan"
    )
    ledger = db.relationship(
        "Ledger", backref="customer", uselist=False, cascade="all, delete-orphan"
    )
    dispatch_logs = db.relationship(
        "MessageDispatchLog", backref="customer", lazy="dynamic", cascade="all, delete-orphan"
    )

    __table_args__ = (
        db.UniqueConstraint("retailer_id", "phone_number", name="uq_customer_per_retailer"),
    )

    def __repr__(self):
        return f"<Customer {self.full_name}>"


class CreditTransaction(db.Model):
    """A single 'deni' (credit sale) issued to a customer."""

    __tablename__ = "credit_transactions"

    transaction_id = db.Column(db.Integer, primary_key=True)
    retailer_id = db.Column(db.Integer, db.ForeignKey("retailers.retailer_id"), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.customer_id"), nullable=False)
    item_description = db.Column(db.String(150), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    transaction_date = db.Column(db.DateTime, default=datetime.utcnow)
    # pending = created offline in the browser but not yet pushed to the server;
    # synced = confirmed persisted server-side.
    sync_status = db.Column(db.String(10), default="synced")  # pending | synced

    def __repr__(self):
        return f"<CreditTransaction {self.transaction_id} KES {self.amount}>"


class Payment(db.Model):
    """A repayment made by a customer against their outstanding balance."""

    __tablename__ = "payments"

    payment_id = db.Column(db.Integer, primary_key=True)
    retailer_id = db.Column(db.Integer, db.ForeignKey("retailers.retailer_id"), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.customer_id"), nullable=False)
    amount_paid = db.Column(db.Numeric(10, 2), nullable=False)
    payment_date = db.Column(db.DateTime, default=datetime.utcnow)
    sync_status = db.Column(db.String(10), default="synced")  # pending | synced

    def __repr__(self):
        return f"<Payment {self.payment_id} KES {self.amount_paid}>"


class Ledger(db.Model):
    """One running balance per (retailer, customer) pair - the 'deni' book."""

    __tablename__ = "ledgers"

    ledger_id = db.Column(db.Integer, primary_key=True)
    retailer_id = db.Column(db.Integer, db.ForeignKey("retailers.retailer_id"), nullable=False)
    customer_id = db.Column(
        db.Integer, db.ForeignKey("customers.customer_id"), nullable=False, unique=True
    )
    outstanding_balance = db.Column(db.Numeric(10, 2), default=0)
    days_overdue = db.Column(db.Integer, default=0)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def recalculate(self):
        """Recompute the outstanding balance from transactions minus payments,
        and days_overdue from the oldest unpaid transaction."""
        def to_decimal(value):
            return value if isinstance(value, Decimal) else Decimal(str(value))

        total_credit = sum(
            (to_decimal(t.amount) for t in self.customer.transactions), start=Decimal("0")
        )
        total_paid = sum(
            (to_decimal(p.amount_paid) for p in self.customer.payments), start=Decimal("0")
        )
        self.outstanding_balance = total_credit - total_paid

        if self.outstanding_balance > 0:
            oldest_unpaid = (
                self.customer.transactions.order_by(
                    CreditTransaction.transaction_date.asc()
                ).first()
            )
            if oldest_unpaid:
                self.days_overdue = (datetime.utcnow() - oldest_unpaid.transaction_date).days
        else:
            self.days_overdue = 0
        self.last_updated = datetime.utcnow()

    def __repr__(self):
        return f"<Ledger customer={self.customer_id} balance={self.outstanding_balance}>"


class ReminderRule(db.Model):
    """A retailer-configurable rule: 'when X days overdue, send this message via channel Y'."""

    __tablename__ = "reminder_rules"

    rule_id = db.Column(db.Integer, primary_key=True)
    retailer_id = db.Column(db.Integer, db.ForeignKey("retailers.retailer_id"), nullable=False)
    trigger_days_overdue = db.Column(db.Integer, nullable=False)
    message_template = db.Column(db.Text, nullable=False)
    channel = db.Column(db.String(10), default="sms")  # sms | whatsapp | both
    active = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return f"<ReminderRule {self.trigger_days_overdue}d {self.channel}>"


class MessageDispatchLog(db.Model):
    """Audit trail of every reminder message sent (or attempted)."""

    __tablename__ = "message_dispatch_log"

    dispatch_id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.customer_id"), nullable=False)
    rule_id = db.Column(db.Integer, db.ForeignKey("reminder_rules.rule_id"), nullable=True)
    channel = db.Column(db.String(10), nullable=False)
    message_content = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(10), default="pending")  # pending | sent | failed
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)

    rule = db.relationship("ReminderRule")

    def __repr__(self):
        return f"<Dispatch {self.dispatch_id} {self.channel} {self.status}>"
