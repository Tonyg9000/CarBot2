"""
Flask web interface for Classic Car Alert Bot
"""

import os
from flask import Flask, render_template, request, jsonify, redirect, url_for
from bot import init_db, get_config, set_config, get_recent_listings, run_check, start_scheduler, scheduler

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "classic-car-bot-secret-2024")

@app.before_request
def ensure_db():
    init_db()

@app.route("/")
def index():
    email = get_config("alert_email") or ""
    listings = get_recent_listings(50)
    is_running = scheduler.running
    next_run = None
    try:
        job = scheduler.get_job("car_check")
        if job and job.next_run_time:
            next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        pass
    return render_template("index.html",
        email=email,
        listings=listings,
        is_running=is_running,
        next_run=next_run,
    )

@app.route("/save-settings", methods=["POST"])
def save_settings():
    email = request.form.get("email", "").strip()
    if email:
        set_config("alert_email", email)
    return redirect(url_for("index"))

@app.route("/run-now", methods=["POST"])
def run_now():
    try:
        run_check()
        return jsonify({"status": "ok", "message": "Check completed successfully."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/listings")
def api_listings():
    return jsonify(get_recent_listings(100))

@app.route("/api/status")
def api_status():
    job = scheduler.get_job("car_check")
    next_run = None
    try:
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()
    except Exception:
        pass
    return jsonify({
        "running": scheduler.running,
        "next_run": next_run,
        "alert_email": get_config("alert_email") or "",
    })

if __name__ == "__main__":
    start_scheduler()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)


# Auto-start scheduler when loaded by gunicorn
import atexit
start_scheduler()
atexit.register(lambda: scheduler.shutdown(wait=False))
