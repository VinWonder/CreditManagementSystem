def register_jobs(app, scheduler):
    from app.reminders.engine import run_ageing_analysis_and_dispatch

    hour = app.config.get("REMINDER_SCAN_HOUR", 8)
    minute = app.config.get("REMINDER_SCAN_MINUTE", 0)

    @scheduler.task("cron", id="daily_reminder_scan", hour=hour, minute=minute)
    def daily_reminder_scan():
        with app.app_context():
            summary = run_ageing_analysis_and_dispatch()
            app.logger.info("Daily reminder scan: %s", summary)
