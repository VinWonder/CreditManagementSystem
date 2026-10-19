from flask import Flask, render_template
from sqlalchemy import inspect, text
from sqlalchemy.exc import ProgrammingError
from config import Config
from app.extensions import db, login_manager, migrate, scheduler


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

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)

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

    # ---- Scheduler: daily automated reminder scan ----
    if app.config["SCHEDULER_ENABLED"] and not scheduler.running:
        scheduler.init_app(app)
        from app.reminders.scheduler_jobs import register_jobs

        register_jobs(app, scheduler)
        scheduler.start()

    # ---- Template filters ----
    @app.template_filter("kes")
    def kes_format(value):
        try:
            return f"KES {float(value):,.2f}"
        except (TypeError, ValueError):
            return "KES 0.00"

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
    }
    with db.engine.begin() as connection:
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE retailers ADD COLUMN {name} {definition}"))
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
            }
            for name, definition in payment_additions.items():
                if name not in payment_columns:
                    connection.execute(
                        text(f"ALTER TABLE payments ADD COLUMN {name} {definition}")
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
