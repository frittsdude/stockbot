# main.py
import os
import threading
import time
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ----- Config -----
ALPHA_KEY = os.getenv("ALPHA_KEY", "").strip()  # set in Render

# In-memory watchlist (resets on restart)
watchlist = set()

# ---------------- Alpha Vantage helpers ----------------
def _is_rate_limited(payload: dict) -> bool:
    # Alpha Vantage rate limit or generic limit notice
    txt = str(payload)
    return ("Note" in payload) or ("Thank you for using Alpha Vantage" in txt)

def _is_info_or_error(payload: dict) -> bool:
    # Other non-quote responses Alpha Vantage uses
    return ("Information" in payload) or ("Error Message" in payload)

def _daily_close(ticker: str, timeout=3.0):
    """
    Returns ("DAILY", float_price) on success,
            ("RATE_LIMIT", None) if limited,
            (None, None) on not found / bad format.
    """
    url = (
        "https://www.alphavantage.co/query"
        f"?function=TIME_SERIES_DAILY&symbol={ticker}&apikey={ALPHA_KEY}"
    )
    try:
        r = requests.get(url, timeout=timeout)
        j = r.json()
    except Exception as e:
        print("TIME_SERIES_DAILY error:", e)
        return (None, None)

    if _is_rate_limited(j):
        return ("RATE_LIMIT", None)
    if _is_info_or_error(j):
        return (None, None)

    ts = j.get("Time Series (Daily)")
    if not ts or not isinstance(ts, dict):
        return (None, None)

    try:
        latest_day = sorted(ts.keys())[-1]
        close_px = float(ts[latest_day]["4. close"])
        return ("DAILY", close_px)
    except Exception as e:
        print("Parse DAILY close error:", e)
        return (None, None)

def fetch_quote(ticker: str, timeout=2.8):
    """
    Try GLOBAL_QUOTE; fallback to TIME_SERIES_DAILY close.
    Returns one of:
      ("RATE_LIMIT", None, None, None)              -> rate limited
      (float_price, float_change, pct_str, "GLOBAL")-> live quote
      (float_price, 0.0, "—", "DAILY")             -> latest daily close
      (None, None, None, None)                      -> no data
    """
    t = ticker.strip().upper()
    gq_url = (
        "https://www.alphavantage.co/query"
        f"?function=GLOBAL_QUOTE&symbol={t}&apikey={ALPHA_KEY}"
    )

    try:
        r = requests.get(gq_url, timeout=timeout)
        j = r.json()
    except Exception as e:
        print("GLOBAL_QUOTE network error:", e)
        j = {}

    if _is_rate_limited(j):
        return ("RATE_LIMIT", None, None, None)
    if _is_info_or_error(j):
        # e.g., invalid symbol or temporary internal notice
        # try the daily fallback anyway
        pass

    quote = j.get("Global Quote") or j.get("GlobalQuote") or {}
    px = quote.get("05. price") or quote.get("05.price")
    chg = quote.get("09. change") or quote.get("09.change")
    pct = quote.get("10. change percent") or quote.get("10.change percent")

    # If Global Quote looks valid
    try:
        if px is not None:
            price_val = float(px)
            change_val = float(chg or 0.0)
            pct_str = pct or "0.00%"
            return (price_val, change_val, pct_str, "GLOBAL")
    except Exception:
        pass  # fall through to daily

    # --- Fallback to daily close ---
    src, close_px = _daily_close(t, timeout=max(2.0, timeout))
    if src == "RATE_LIMIT":
        return ("RATE_LIMIT", None, None, None)
    if close_px is not None:
        return (close_px, 0.0, "—", "DAILY")

    return (None, None, None, None)

# ---------------- Slack helpers ----------------
def post_to_response_url(response_url: str, text: str):
    if not response_url:
        return
    try:
        requests.post(
            response_url,
            json={"response_type": "in_channel", "text": text},
            timeout=10,
        )
    except Exception as e:
        print("response_url post error:", e)

def build_price_text(ticker: str) -> str:
    t = ticker.upper()
    if not ALPHA_KEY:
        return f"{t}: Alpha Vantage API key missing."
    price, change, pct, src = fetch_quote(ticker)
    if price == "RATE_LIMIT":
        return f"{t}: Alpha Vantage free-tier rate limit hit. Try again in ~15–60s."
    if price is None:
        return f"{t}: No quote returned for that symbol right now."
    if src == "GLOBAL":
        return f"{t}: ${price:.2f} (Δ {change:.4f}, {pct})"
    return f"{t}: ${price:.2f} (latest daily close)"

# ---------------- HTTP routes ----------------
@app.route("/")
def home():
    return "StockBot is up. Use /price and /watchlist in Slack."

@app.route("/health")
def health():
    return "ok"

# ---- /price ----
@app.route("/slack/price", methods=["POST"])
def cmd_price():
    text = (request.form.get("text") or "").strip()
    response_url = request.form.get("response_url")

    if not text:
        return jsonify(
            response_type="in_channel",
            text="Usage: `/price AAPL`",
        ), 200

    # Fast path (under 3s)
    q = fetch_quote(text, timeout=2.4)
    if q and q[0] not in (None, "RATE_LIMIT"):
        price, change, pct, src = q
        if src == "GLOBAL":
            msg = f"{text.upper()}: ${price:.2f} (Δ {change:.4f}, {pct})"
        else:
            msg = f"{text.upper()}: ${price:.2f} (latest daily close)"
        return jsonify(response_type="in_channel", text=msg), 200

    # Slow path / rate limit: reply later via response_url
    threading.Thread(
        target=lambda: post_to_response_url(response_url, build_price_text(text)),
        daemon=True,
    ).start()
    return jsonify(response_type="ephemeral", text="Working…"), 200

# ---- /watchlist ----
def build_watchlist_text() -> str:
    if not watchlist:
        return "📭 Watchlist is empty."
    lines = []
    for t in sorted(watchlist):
        q = fetch_quote(t)
        if q and q[0] not in (None, "RATE_LIMIT"):
            price, change, pct, src = q
            if src == "GLOBAL":
                lines.append(f"{t}: ${price:.2f} (Δ {change:.4f}, {pct})")
            else:
                lines.append(f"{t}: ${price:.2f} (latest daily close)")
        elif q[0] == "RATE_LIMIT":
            lines.append(f"{t}: rate limited (try later)")
        else:
            lines.append(f"{t}: (no data)")
        # Gentle throttle to avoid blasting the free API
        time.sleep(0.4)
    return "📊 Watchlist:\n" + "\n".join(lines)

@app.route("/slack/watchlist", methods=["POST"])
def cmd_watchlist():
    parts = (request.form.get("text") or "").strip().split()
    response_url = request.form.get("response_url")

    if not parts:
        # Default to list
        threading.Thread(
            target=lambda: post_to_response_url(response_url, build_watchlist_text()),
            daemon=True,
        ).start()
        return jsonify(response_type="ephemeral", text="Working…"), 200

    action = parts[0].lower()
    tickers = [p.upper() for p in parts[1:]] if len(parts) > 1 else []

    if action == "add" and tickers:
        for t in tickers:
            watchlist.add(t)
        return jsonify(response_type="in_channel", text=f"✅ Added: {', '.join(tickers)}"), 200

    if action == "remove" and tickers:
        removed, missing = [], []
        for t in tickers:
            (removed if t in watchlist else missing).append(t)
            if t in watchlist:
                watchlist.remove(t)
        msg = []
        if removed:
            msg.append(f"🗑️ Removed: {', '.join(removed)}")
        if missing:
            msg.append(f"Not in list: {', '.join(missing)}")
        return jsonify(response_type="in_channel", text=(". ".join(msg) or "No changes.")), 200

    if action == "list":
        threading.Thread(
            target=lambda: post_to_response_url(response_url, build_watchlist_text()),
            daemon=True,
        ).start()
        return jsonify(response_type="ephemeral", text="Working…"), 200

    return jsonify(
        response_type="in_channel",
        text="Invalid. Use: `/watchlist add TICKER [TICKER...]`, `/watchlist remove TICKER [TICKER...]`, `/watchlist list`",
    ), 200

# --------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
