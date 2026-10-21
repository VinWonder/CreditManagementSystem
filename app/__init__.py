import os
import secrets

from flask import Flask, render_template
from flask_login import user_logged_in
from sqlalchemy import inspect, text
from sqlalchemy.exc import ProgrammingError
from config import Config
from app.extensions import db, login_manager, migrate
from app.timezones import format_nairobi_datetime


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    if app.config["APP_ENV"] == "production":
        if not app.config.get("SECRET_KEY") or app.config["SECRET_KEY"] == "dev-secret-key-change-me":
            raise RuntimeError("Set a unique SECRET_KEY before running in production.")
        if not app.config.get("SESSION_COOKIE_SECURE"):
            raise RuntimeError("Enable SESSION_COOKIE_SECURE when running behind HTTPS.")
        if not app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgresql+"):
            raise RuntimeError("Production must use PostgreSQL, not SQLite.")
        if not app.config.get("SMTP_HOST"):
            raise RuntimeError("Set SMTP_HOST so new retailers can receive email verification codes.")

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)

    # One ID per server start. The reloader's child process inherits it through
    # the environment, so code reloads keep sessions while a real restart ends them.
    app.config["SERVER_BOOT_ID"] = os.environ.setdefault(
        "DUKA_SERVER_BOOT_ID", secrets.token_hex(16)
    )

    @user_logged_in.connect_via(app)
    def stamp_session_with_boot_id(sender, user, **extra):
        from flask import session

        session["_server_boot_id"] = app.config["SERVER_BOOT_ID"]

    @app.before_request
    def end_sessions_from_before_restart():
        """Sign out sessions created before the server last started."""
        from flask import g, session

        if not app.config.get("SESSION_ENDS_ON_RESTART") or not session.get("_user_id"):
            return
        if session.get("_server_boot_id") != app.config["SERVER_BOOT_ID"]:
            session.clear()
            g.pop("_login_user", None)

    @app.before_request
    def refresh_session_lifetime():
        """Slide the idle timeout forward while the user stays active."""
        from flask import session

        if session.get("_user_id"):
            session.permanent = True
            session.modified = True
    @app.errorhandler(ProgrammingError)
    def handle_database_programming_error(error):
        db.session.rollback()
        app.logger.error(
            "Database programming error (%s). Verify that database migrations are current.",
            type(error.orig).__name__,
        )
        return render_template("errors/database.html"), 503

    from app.models import Retailer, Shopkeeper

    @login_manager.user_loader
    def load_user(user_id):
        if user_id.startswith("shopkeeper:"):
            shopkeeper = db.session.get(Shopkeeper, int(user_id.split(":", 1)[1]))
            return shopkeeper if shopkeeper and shopkeeper.active else None
        return db.session.get(Retailer, int(user_id))

    # ---- Blueprints ----
    from app.auth.routes import auth_bp
    from app.auth.forms import LogoutForm
    from app.main.routes import main_bp
    from app.customers.routes import customers_bp
    from app.transactions.routes import transactions_bp
    from app.payments.routes import payments_bp
    from app.ledger.routes import ledger_bp
    from app.reminders.routes import reminders_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(customers_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(ledger_bp)
    app.register_blueprint(reminders_bp)

    from app.auth.email import send_test_email_command

    app.cli.add_command(send_test_email_command)

    @app.context_processor
    def inject_logout_form():
        return {"logout_form": LogoutForm()}

    @app.context_processor
    def inject_pending_payments():
        """Surface payments a shopkeeper logged that the retailer still needs to verify."""
        from flask_login import current_user

        from app.models import Payment

        if not current_user.is_authenticated or not getattr(current_user, "is_admin", False):
            return {"pending_approval_count": 0}
        count = Payment.query.filter_by(
            retailer_id=current_user.retailer_id, approval_status="pending"
        ).count()
        return {"pending_approval_count": count}

    # The reminder scan scheduler was removed: retailers send reminders
    # themselves from their own WhatsApp or SMS app.

    # ---- Template filters ----
    @app.template_filter("kes")
    def kes_format(value):
        try:
            return f"KES {float(value):,.2f}"
        except (TypeError, ValueError):
            return "KES 0.00"

    @app.template_filter("nairobi")
    def nairobi_datetime(value, format_string="%d %b %Y %H:%M"):
        return format_nairobi_datetime(value, format_string)

    with app.app_context():
        if app.config["AUTO_CREATE_DB"] and db.engine.url.drivername.startswith("sqlite"):
            _upgrade_local_auth_schema()
            db.create_all()

    return app


def _upgrade_local_auth_schema():
    """Add new retailer identity columns for existing SQLite demo databases."""
    if not db.engine.url.drivername.startswith("sqlite"):
        return

    inspector = inspect(db.engine)
    if "retailers" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("retailers")}
    additions = {
        "email": "VARCHAR(255)",
        "phone_verified_at": "DATETIME",
        "logo_filename": "VARCHAR(255)",
        "email_verified_at": "DATETIME",
    }
    with db.engine.begin() as connection:
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE retailers ADD COLUMN {name} {definition}"))
        if "email_verified_at" not in columns:
            # Accounts created before email verification keep working.
            connection.execute(
                text(
                    "UPDATE retailers SET email_verified_at = COALESCE(created_at, CURRENT_TIMESTAMP)"
                )
            )
        if "payments" in inspector.get_table_names():
            payment_columns = {
                column["name"] for column in inspector.get_columns("payments")
            }
            payment_additions = {
                "approval_status": "VARCHAR(10) NOT NULL DEFAULT 'approved'",
                "submitted_by_shopkeeper_id": "INTEGER",
                "reviewed_by_retailer_id": "INTEGER",
                "reviewed_at": "DATETIME",
                "review_note": "VARCHAR(255)",
                "payment_method": "VARCHAR(20)",
                "payment_reference": "VARCHAR(40)",
            }
            for name, definition in payment_additions.items():
                if name not in payment_columns:
                    connection.execute(
                        text(f"ALTER TABLE payments ADD COLUMN {name} {definition}")
                    )
        local_table_additions = {
            "customers": {
                "shop_name": "VARCHAR(100)",
                "accepts_stk_prompts": "BOOLEAN NOT NULL DEFAULT 0",
            },
            "message_dispatch_log": {"failure_reason": "VARCHAR(500)"},
        }
        for table_name, table_additions in local_table_additions.items():
            if table_name not in inspector.get_table_names():
                continue
            table_columns = {
                column["name"] for column in inspector.get_columns(table_name)
            }
            for name, definition in table_additions.items():
                if name not in table_columns:
                    connection.execute(
                        text(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")
                    )
        connection.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS uq_retailers_email ON retailers (email)")
        )
        connection.execute(
            text(
                "UPDATE retailers SET phone_verified_at = COALESCE(created_at, CURRENT_TIMESTAMP) "
                "WHERE phone_verified_at IS NULL AND email IS NULL"
            )
        )
