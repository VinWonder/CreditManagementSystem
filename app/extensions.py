from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_apscheduler import APScheduler

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()
scheduler = APScheduler()

login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"
login_manager.session_protection = "strong"
