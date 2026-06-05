"""
Gunicorn worker init — starts the scheduler in each worker process.
Import this in gunicorn config or call at app startup.
"""
from bot import init_db, start_scheduler

init_db()
start_scheduler()
