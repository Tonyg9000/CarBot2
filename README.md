# 🏎 Classic Car Alert Bot

Monitors **Craigslist**, **eBay Motors**, **AutoTrader**, and **Cars.com** for classic cars
(1900–2005) and emails you the moment new listings appear.

---

## How It Works

- Checks all 4 platforms every **15 minutes**
- Detects classic cars by scanning for years between 1900–2005 in listing titles
- Tracks already-seen listings in a local SQLite database (no duplicate emails)
- Sends a beautiful HTML email digest for every batch of new listings
- Web dashboard to configure your email, view recent finds, and trigger manual scans

---

## Quick Deploy (Railway — Recommended, Free Tier Available)

1. **Create a free account** at [railway.app](https://railway.app)

2. **Push this project to GitHub:**
   ```bash
   git init
   git add .
   git commit -m "Classic Car Bot"
   gh repo create classic-car-bot --public --push
   ```

3. **In Railway:** New Project → Deploy from GitHub repo → select your repo

4. **Set environment variables** in Railway's Variables tab:

   | Variable | Value |
   |---|---|
   | `SMTP_HOST` | `smtp.gmail.com` |
   | `SMTP_PORT` | `587` |
   | `SMTP_USER` | `your.gmail@gmail.com` |
   | `SMTP_PASS` | *(your Gmail App Password — see below)* |
   | `ALERT_EMAIL` | `your.email@example.com` |
   | `CHECK_INTERVAL_MINUTES` | `15` |

5. Railway auto-deploys. Visit your app URL to see the dashboard!

---

## Alternative: Deploy on Render (Also Free)

1. Create account at [render.com](https://render.com)
2. New → Web Service → Connect your GitHub repo
3. Render auto-detects `render.yaml` and configures everything
4. Add your SMTP environment variables in the Render dashboard

---

## Gmail App Password Setup (Required)

Google blocks plain passwords for SMTP. You need an **App Password**:

1. Go to your Google Account → **Security**
2. Enable **2-Step Verification** (if not already on)
3. Go to **Security → App passwords**
4. Select app: "Mail", device: "Other" → type "Classic Car Bot"
5. Copy the 16-character password → use as `SMTP_PASS`

---

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=you@gmail.com
export SMTP_PASS=your_app_password
export ALERT_EMAIL=you@gmail.com

# Run locally
python app.py
# → Visit http://localhost:5000
```

---

## Project Structure

```
classic-car-bot/
├── app.py           # Flask web app + routes
├── bot.py           # Scraper logic, email sender, scheduler
├── templates/
│   └── index.html   # Web dashboard
├── requirements.txt
├── Procfile         # For Railway/Heroku
├── railway.toml     # Railway config
└── render.yaml      # Render config
```

---

## Customization

**Change check interval:** Set `CHECK_INTERVAL_MINUTES` env var (default: 15)

**Add/remove Craigslist cities:** Edit `CRAIGSLIST_CITIES` list in `bot.py`

**Change year range:** Edit `CLASSIC_YEAR_MIN` / `CLASSIC_YEAR_MAX` in `bot.py`

**Add keyword filters** (e.g., only Mustangs): Add to the `is_classic()` function in `bot.py`

---

## Notes on Platform Coverage

| Platform | Method | Coverage |
|---|---|---|
| Craigslist | **RSS feed** (official) | 15 major US cities |
| eBay Motors | HTML scraping | All US listings, newest first |
| AutoTrader | HTML scraping | Nationwide |
| Cars.com | HTML scraping | Nationwide |

> **Note:** AutoTrader and Cars.com may update their HTML structure over time. If scraping
> breaks, the Craigslist and eBay sources will continue working. Check the dashboard logs.

---

## License

MIT — use freely, modify as needed.
