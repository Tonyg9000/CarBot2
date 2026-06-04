# gunicorn.conf.py
import os

workers = 1          # Keep 1 worker so the scheduler only runs once
threads = 4
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
timeout = 120
loglevel = "info"
