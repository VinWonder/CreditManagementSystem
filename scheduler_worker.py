"""Run the single production reminder scheduler process."""
from threading import Event

from app import create_app
from app.extensions import scheduler


app = create_app()
if not scheduler.running:
    raise RuntimeError("Set SCHEDULER_ENABLED=true for the scheduler worker process.")

try:
    Event().wait()
except KeyboardInterrupt:
    scheduler.shutdown(wait=False)
