# main.py
import os
import threading
import time
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ----- Config -----
ALPHA_KEY = os.getenv("ALPHA_KEY", "FZTRK2YCAJQIWTM1")  # set in Render

# Simple in-memory watchlist (resets on restart)
watchlist = set()

# -------- Alpha Vantage (with fallback + clearer errors) --------
def _daily_close(ticker: str, timeout=3.0):
    url = (
        "https://www.alphavantage.co/query"
        f"?function=TIME_SERIES_DAILY&symbol={ticker}&apikey={ALPHA_KEY}"
    )
    r = requests.get(url, timeout=timeout)
    j = r.json()
    if "Note" in j or "Thank you for using Alpha Vantage" in str(j):
        return ("RATE_LIMIT", None)
    ts = j.get("Time Series (Daily)")
    if not ts:
        return (None, None)
    latest_day = sorted(ts.keys())[-1]
    close_px = ts[latest_day].get("4. close")
    try:
        return ("DAILY", float(close_px))
    except Exception:
        return (None, None)

def fetch_quote(ticker: str, timeout=2.8):
    """
    Try GLOBAL_QUOTE first; if missing, fall back to TIME_SERIES_DAILY close.
    Returns (price, change, pct_str, source) or special ('RATE_LIMIT', None, None, None).
    """
    t = ticker.strip().upper()
    gq_url = (
        "https://www.alphavantage.co/query"
        f"?function=GLOBAL_QUOTE&symbol={t}&apikey={ALPHA_KEY}"
    )
    try:
        r = requests.get(gq_url, timeout=timeout)
        j = r.json()
        if "Note" in j or "Thank you for using Alpha Vantage" in str(j):
            return ("RATE_LIMIT", None, None, None)

        quote = j.get("Global Quote", {}) or {}
        px = quote.get("05. price")
        chg = quote.get("09. change")
        pct = quote.get("10. change percent")

        # If Global Quote is valid, return it
        if px:
            try:
                return (float(px), float(chg or 0.0), pct or "0.00%", "GLOBAL")
            except Exception:
                pass

        # Fallback to daily close if Global Quote is empty
        src, close_px = _daily_close(t, timeout=max(2.0, timeout))
        if src == "RATE_LIMIT":
            return ("RATE_LIMIT", None, None, None)
        if close_px is not None:
            return (close_px, 0.0, "—", src)  # no intraday change data here

        # Nothing found
        return (None, None, None, None)

    except Exception as e:
        print("AlphaVantage error:", e)
        return (None, None, None, None)

def build_price_text(ticker: str) -> str:
    price, change, pct, src = fetch_quote(ticker)
    t = ticker.upper()
    if price == "RATE_LIMIT":
        return f"{t}: Rate limit hit on Alpha Vantage (free tier). Try again in ~15–60s."
    if price is None:
        return f"{t}: No quote returned for that symbol right now."
    # Global quote shows change; daily fallback shows close only
    if src == "GLOBAL":
        return f"{t}: ${price:.2f} (Δ {change:.4f}, {pct})"
    else:
        return f"{t}: ${price:.2f} (latest daily close)"

# -------- Delayed in-channel response via response_url --------
def post_to_response_url(response_url: str, text: str):
    try:
        requests.post(
            response_url,
            json={"response_type": "in_channel", "text": text},
            timeout=10,
        )
    except Exception as e:
        print("response_url post error:", e)

# --------------------------------------------------------------
@app.route("/")
def home():
    return "StockBot is up. Use /price and /watchlist in Slack."

# ===================== /price =====================
@app.route("/slack/price", methods=["POST"])
def cmd_price():
    text = (request.form.get("text") or "").strip()
    response_url = request.form.get("response_url")

    if not text:
        return jsonify(
            response_type="in_channel",
            text="Usage: `/price AAPL`",
        ), 200

    # Try fast path (finish < 3 seconds) so Slack shows the public message immediately
    q = fetch_quote(text, timeout=2.4)
    if q and q[0] not in (None, "RATE_LIMIT"):
        price, change, pct, src = q
        if src == "GLOBAL":
            return jsonify(
                response_type="in_channel",
                text=f"{text.upper()}: ${price:.2f} (Δ {change:.4f}, {pct})",
            ), 200
        else:
            return jsonify(
                response_type="in_channel",
                text=f"{text.upper()}: ${price:.2f} (latest daily close)",
            ), 200

    # If not ready in time or rate-limited
    threading.Thread(
        target=lambda: post_to_response_url(response_url, build_price_text(text)),
        daemon=True,
    ).start()
    return jsonify(response_type="ephemeral", text="Working…"), 200

# ===================== /watchlist =====================
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
            lines.append(f"{t}: Rate limit hit (try later).")
        else:
            lines.append(f"{t}: (no data)")
    return "📊 Watchlist:\n" + "\n".join(lines)

@app.route("/slack/watchlist", methods=["POST"])
def cmd_watchlist():
    parts = (request.form.get("text") or "").strip().split()
    response_url = request.form.get("response_url")

    if not parts:
        return jsonify(
            response_type="in_channel",
            text="Usage: `/watchlist add TICKER [TICKER...]`, `/watchlist remove TICKER [TICKER...]`, `/watchlist list`",
        ), 200

    action = parts[0].lower()
    tickers = [p.upper() for p in parts[1:]] if len(parts) > 1 else []

    if action == "add" and tickers:
        for t in tickers:
            watchlist.add(t)
        return jsonify(
            response_type="in_channel",
            text=f"✅ Added: {', '.join(tickers)}",
        ), 200

    if action == "remove" and tickers:
        removed, missing = [], []
        for t in tickers:
            if t in watchlist:
                watchlist.remove(t)
                removed.append(t)
            else:
                missing.append(t)
        msg = []
        if removed:
            msg.append(f"🗑️ Removed: {', '.join(removed)}")
        if missing:
            msg.append(f"Not in list: {', '.join(missing)}")
        return jsonify(
            response_type="in_channel",
            text=(". ".join(msg) or "No changes."),
        ), 200

    if action == "list":
        def do_list():
            post_to_response_url(response_url, build_watchlist_text())
        threading.Thread(target=do_list, daemon=True).start()
        return jsonify(response_type="ephemeral", text="Working…"), 200

    return jsonify(
        response_type="in_channel",
        text="Invalid command. Use: `/watchlist add TICKER [TICKER...]`, `/watchlist remove TICKER [TICKER...]`, `/watchlist list`",
    ), 200

# --------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
