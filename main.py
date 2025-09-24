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

# -------- Alpha Vantage --------
def fetch_quote(ticker: str, timeout=2.4):
    """Return (price, change, pct_str) or None."""
    t = ticker.strip().upper()
    url = (
        "https://www.alphavantage.co/query"
        f"?function=GLOBAL_QUOTE&symbol={t}&apikey={ALPHA_KEY}"
    )
    try:
        r = requests.get(url, timeout=timeout)
        data = r.json().get("Global Quote", {})
        px = data.get("05. price")
        chg = data.get("09. change")
        pct = data.get("10. change percent")
        if not px:
            return None
        return float(px), (float(chg) if chg not in (None, "") else 0.0), pct or "0.00%"
    except Exception as e:
        print("AlphaVantage error:", e)
        return None

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
def build_price_text(ticker: str) -> str:
    q = fetch_quote(ticker)
    if not q:
        return f"{ticker.upper()}: (no data)"
    price, change, pct = q
    return f"{ticker.upper()}: ${price:.2f} (Δ {change:.4f}, {pct})"

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
    start = time.time()
    q = fetch_quote(text, timeout=2.4)
    if q:
        price, change, pct = q
        return jsonify(
            response_type="in_channel",
            text=f"{text.upper()}: ${price:.2f} (Δ {change:.4f}, {pct})",
        ), 200

    # If it wasn't ready in time, ack quickly (ephemeral) and finish via response_url
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
        if q:
            price, change, pct = q
            lines.append(f"{t}: ${price:.2f} (Δ {change:.4f}, {pct})")
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
        # public, immediate
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
        # Listing can be slow (multiple quotes). Do delayed public post if needed.
        # Try to do it fast; if not, fall back to response_url.
        def do_list():
            post_to_response_url(response_url, build_watchlist_text())

        thread = threading.Thread(target=do_list, daemon=True)
        thread.start()
        return jsonify(response_type="ephemeral", text="Working…"), 200

    return jsonify(
        response_type="in_channel",
        text="Invalid command. Use: `/watchlist add TICKER [TICKER...]`, `/watchlist remove TICKER [TICKER...]`, `/watchlist list`",
    ), 200

# --------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
