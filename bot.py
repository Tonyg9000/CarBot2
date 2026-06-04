"""
Classic Car Alert Bot
Monitors Craigslist RSS, eBay Motors, and scrapes AutoTrader/Cars.com
for classic cars (1900-2005) and sends email alerts.
"""

import os
import re
import json
import time
import hashlib
import logging
import smtplib
import sqlite3
import feedparser
import requests
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "seen_listings.db")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL_MINUTES", "15"))

CLASSIC_YEAR_MIN = 1900
CLASSIC_YEAR_MAX = 2005

# ── Database ──────────────────────────────────────────────────────────────────

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            id TEXT PRIMARY KEY,
            title TEXT,
            url TEXT,
            source TEXT,
            price TEXT,
            year TEXT,
            seen_at TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    con.commit()
    con.close()

def already_seen(listing_id: str) -> bool:
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT 1 FROM seen WHERE id=?", (listing_id,)).fetchone()
    con.close()
    return row is not None

def mark_seen(listing: dict):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT OR IGNORE INTO seen(id,title,url,source,price,year,seen_at) VALUES(?,?,?,?,?,?,?)",
        (listing["id"], listing["title"], listing["url"], listing["source"],
         listing.get("price",""), listing.get("year",""), datetime.utcnow().isoformat())
    )
    con.commit()
    con.close()

def get_recent_listings(limit=50):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT title,url,source,price,year,seen_at FROM seen ORDER BY seen_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    con.close()
    return [{"title":r[0],"url":r[1],"source":r[2],"price":r[3],"year":r[4],"seen_at":r[5]} for r in rows]

def get_config(key, default=None):
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    con.close()
    return row[0] if row else default

def set_config(key, value):
    con = sqlite3.connect(DB_PATH)
    con.execute("INSERT OR REPLACE INTO config(key,value) VALUES(?,?)", (key, value))
    con.commit()
    con.close()

# ── Year extraction ───────────────────────────────────────────────────────────

def extract_year(text: str):
    """Return integer year if found in text and within classic range, else None."""
    matches = re.findall(r'\b(19\d{2}|200[0-5])\b', text)
    for m in matches:
        y = int(m)
        if CLASSIC_YEAR_MIN <= y <= CLASSIC_YEAR_MAX:
            return y
    return None

def is_classic(title: str, description: str = "") -> tuple[bool, str]:
    combined = f"{title} {description}"
    year = extract_year(combined)
    if year:
        return True, str(year)
    return False, ""

# ── Craigslist RSS ────────────────────────────────────────────────────────────

CRAIGSLIST_CITIES = [
    "newyork", "losangeles", "chicago", "houston", "phoenix",
    "philadelphia", "sanantonio", "sandiego", "dallas", "sfbay",
    "seattle", "miami", "atlanta", "boston", "denver",
]

def scrape_craigslist():
    results = []
    for city in CRAIGSLIST_CITIES:
        url = f"https://{city}.craigslist.org/search/cta?format=rss&auto_year_min={CLASSIC_YEAR_MIN}&auto_year_max={CLASSIC_YEAR_MAX}"
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title = entry.get("title", "")
                link = entry.get("link", "")
                summary = entry.get("summary", "")
                classic, year = is_classic(title, summary)
                if not classic:
                    continue
                price_match = re.search(r'\$[\d,]+', title + summary)
                price = price_match.group(0) if price_match else "N/A"
                listing_id = hashlib.md5(link.encode()).hexdigest()
                results.append({
                    "id": listing_id,
                    "title": title,
                    "url": link,
                    "source": f"Craigslist ({city})",
                    "price": price,
                    "year": year,
                    "description": BeautifulSoup(summary, "html.parser").get_text()[:300],
                })
        except Exception as e:
            log.warning(f"Craigslist {city} error: {e}")
        time.sleep(0.3)
    return results

# ── eBay Motors ───────────────────────────────────────────────────────────────

def scrape_ebay():
    results = []
    url = (
        "https://www.ebay.com/sch/i.html?_nkw=classic+car&_sacat=6001"
        f"&_udlo=&_udhi=&LH_ItemCondition=3&LH_BIN=1"
        f"&_mPrRngCBx=1&_stpos=&_sop=10&_pgn=1&_skc=0"
    )
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ClassicCarBot/1.0)"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select(".s-item")
        for item in items[:30]:
            title_el = item.select_one(".s-item__title")
            link_el = item.select_one(".s-item__link")
            price_el = item.select_one(".s-item__price")
            if not title_el or not link_el:
                continue
            title = title_el.get_text(strip=True)
            link = link_el.get("href", "").split("?")[0]
            price = price_el.get_text(strip=True) if price_el else "N/A"
            classic, year = is_classic(title)
            if not classic:
                continue
            listing_id = hashlib.md5(link.encode()).hexdigest()
            results.append({
                "id": listing_id,
                "title": title,
                "url": link,
                "source": "eBay Motors",
                "price": price,
                "year": year,
                "description": title,
            })
    except Exception as e:
        log.warning(f"eBay scrape error: {e}")
    return results

# ── AutoTrader ────────────────────────────────────────────────────────────────

def scrape_autotrader():
    results = []
    url = (
        f"https://www.autotrader.com/cars-for-sale/all-cars"
        f"?startYear={CLASSIC_YEAR_MIN}&endYear={CLASSIC_YEAR_MAX}"
        f"&sortBy=datelistedDESC&numRecords=25"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=12)
        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("[data-cmp='inventoryListing']") or soup.select(".listing-container")
        for card in cards[:20]:
            title_el = card.select_one("h2") or card.select_one(".title")
            link_el = card.select_one("a")
            price_el = card.select_one(".price-block") or card.select_one("[data-cmp='pricingBlock']")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = link_el.get("href", "") if link_el else ""
            link = f"https://www.autotrader.com{href}" if href.startswith("/") else href
            price = price_el.get_text(strip=True) if price_el else "N/A"
            classic, year = is_classic(title)
            if not classic:
                continue
            listing_id = hashlib.md5((title + link).encode()).hexdigest()
            results.append({
                "id": listing_id,
                "title": title,
                "url": link or "https://autotrader.com",
                "source": "AutoTrader",
                "price": price,
                "year": year,
                "description": title,
            })
    except Exception as e:
        log.warning(f"AutoTrader scrape error: {e}")
    return results

# ── Cars.com ──────────────────────────────────────────────────────────────────

def scrape_carsdotcom():
    results = []
    url = (
        f"https://www.cars.com/shopping/results/"
        f"?maximum_distance=all&mileage_max=&monthly_payment="
        f"&price_max=&price_min=&sort=listed_at_desc"
        f"&stock_type=all&year_max={CLASSIC_YEAR_MAX}&year_min={CLASSIC_YEAR_MIN}&zip="
    )
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ClassicCarBot/1.0)"}
    try:
        resp = requests.get(url, headers=headers, timeout=12)
        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".vehicle-card") or soup.select("[data-qa='vehicle-card']")
        for card in cards[:20]:
            title_el = card.select_one(".vehicle-card-main-specs h2") or card.select_one("h2")
            link_el = card.select_one("a.vehicle-card-link") or card.select_one("a")
            price_el = card.select_one(".primary-price") or card.select_one(".price-section")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = link_el.get("href", "") if link_el else ""
            link = f"https://www.cars.com{href}" if href.startswith("/") else href
            price = price_el.get_text(strip=True) if price_el else "N/A"
            classic, year = is_classic(title)
            if not classic:
                continue
            listing_id = hashlib.md5((title + link).encode()).hexdigest()
            results.append({
                "id": listing_id,
                "title": title,
                "url": link or "https://cars.com",
                "source": "Cars.com",
                "price": price,
                "year": year,
                "description": title,
            })
    except Exception as e:
        log.warning(f"Cars.com scrape error: {e}")
    return results

# ── Email ─────────────────────────────────────────────────────────────────────

def send_email_alert(listings: list, recipient: str):
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")

    if not smtp_user or not smtp_pass:
        log.warning("SMTP credentials not set — skipping email.")
        return

    subject = f"🚗 {len(listings)} New Classic Car Listing{'s' if len(listings)>1 else ''} Found!"

    html_items = ""
    for l in listings:
        html_items += f"""
        <div style="border:1px solid #d4a843;border-radius:8px;padding:16px;margin-bottom:16px;background:#1a1a1a;">
          <div style="font-size:11px;color:#d4a843;text-transform:uppercase;letter-spacing:2px;">{l['source']} &nbsp;·&nbsp; {l.get('year','')}</div>
          <div style="font-size:18px;font-weight:bold;color:#f0e6c8;margin:8px 0;">{l['title']}</div>
          <div style="font-size:22px;color:#d4a843;font-weight:bold;">{l['price']}</div>
          <a href="{l['url']}" style="display:inline-block;margin-top:12px;padding:8px 20px;background:#d4a843;color:#0d0d0d;text-decoration:none;border-radius:4px;font-weight:bold;font-size:13px;">View Listing →</a>
        </div>"""

    html = f"""
    <html><body style="background:#0d0d0d;font-family:Georgia,serif;padding:32px;color:#f0e6c8;">
      <div style="max-width:600px;margin:auto;">
        <div style="text-align:center;margin-bottom:32px;">
          <div style="font-size:36px;">🏎</div>
          <h1 style="color:#d4a843;font-size:28px;margin:8px 0;">Classic Car Alert</h1>
          <p style="color:#888;font-size:14px;">New listings matching 1900–2005 · {datetime.utcnow().strftime('%b %d, %Y %H:%M UTC')}</p>
        </div>
        {html_items}
        <p style="color:#555;font-size:12px;text-align:center;margin-top:32px;">
          You're receiving this because you set up Classic Car Bot.<br>
          Listings sourced from Craigslist, eBay Motors, AutoTrader, and Cars.com.
        </p>
      </div>
    </body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = recipient
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, recipient, msg.as_string())
        log.info(f"Email sent to {recipient} with {len(listings)} listings.")
    except Exception as e:
        log.error(f"Email send failed: {e}")

# ── Main check loop ───────────────────────────────────────────────────────────

def run_check():
    recipient = get_config("alert_email") or os.environ.get("ALERT_EMAIL", "")
    if not recipient:
        log.warning("No alert email configured. Skipping check.")
        return

    log.info("Running classic car check across all sources...")
    all_listings = []
    all_listings += scrape_craigslist()
    all_listings += scrape_ebay()
    all_listings += scrape_autotrader()
    all_listings += scrape_carsdotcom()

    new_listings = []
    for listing in all_listings:
        if not already_seen(listing["id"]):
            mark_seen(listing)
            new_listings.append(listing)

    log.info(f"Found {len(new_listings)} new listings (of {len(all_listings)} total scraped).")

    if new_listings:
        send_email_alert(new_listings, recipient)

# ── Scheduler init ────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler()

def start_scheduler():
    init_db()
    scheduler.add_job(run_check, "interval", minutes=CHECK_INTERVAL, id="car_check", replace_existing=True)
    scheduler.start()
    log.info(f"Scheduler started — checking every {CHECK_INTERVAL} minutes.")
