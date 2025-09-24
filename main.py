# main.py
import os
import threading
import time
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# -------------------- Config --------------------
ALPHA_KEY = os.getenv("ALPHA_KEY", "").strip()  # set in Render
REQUEST_TIMEOUT = 2.5  # seconds (keeps Slack fast)

# Simple in-memory watchlist (resets on restart)
watchlist = set()


# ---------------- Alpha Vantage helpers ----------------
def fetch_quote_raw(ticker: str, timeout: float = REQUEST_TIMEOUT) -> dict:
    """
    Call Alpha Vantage GLOBAL_QUOTE and return the raw JSON dict.
    This may include: 'Global Quote', or a 'Note' (rate limit), or an 'Error Message'.
    """
    t = (ticker or "").strip().upper()
    url = (
        "https://www.alphavantage.co/query"
        f"?function=GLOBAL_QUOTE&symbol={t}&apikey={ALPHA_KEY}"
    )
    r = requests.get(url, timeout=timeout)
    return r.json()


def parse_global_quote(data: dict):
    """
    Convert Alpha Vantage JSON to (price, change, pct) or raise a user-friendly string.
    """
    if not isinstance(data, dict):
        raise Exception("Bad response")

    if "Note" in data:
        # Free tier: 5 req/min; 500/day
        raise Exception("⚠️ Alpha Vantage rate limit hit. Try again in ~60 seconds.")
    if "Error Message" in data:
        raise Exception("❌ Invalid API call / symbol.")

    quote = data.get("Global Quote", {})
    px = quote.get("05. price")
    if not px:
        raise Exception("No quote returned for that symbol right now.")

    chg = quote.get("09. change")
    pct = quote.get("10. change percent") or "0.00%"
    return float(px), (float(chg) if chg not in (None, "") else 0.0), pct


def fetch_quote(ticker: str, timeout: float = REQUEST_TIMEOUT):
    """
    Returns either a tuple (price, change, pct) OR a dict {'error': 'message'} when something is off.
    """
    try:
        data = fetch_quote_raw(ticker, timeout=timeout)
        return parse_global_quote(data)
    except Exception as e:
        msg = str(e) or "Problem fetching quote."
        # Log to server logs too
        print(f"fetch_quote error for {ticker}: {msg}")
        return {"error": msg}


# ---------------- Slack response_url helpers ----------------
def post_to_response_url(response_url: str, text: str, in_channel: bool = True):
    """
    Post a follow-up message using Slack's response_url.
    in_channel=True means visible to everyone in the channel.
    """
    if not response_url:
        print("Missing response_url for follow-up post.")
        return
    try:
        requests.post(
            response_url,
            json={"response_type": "in_channel" if in_channel else "ephemeral", "text": text},
            timeout=10,
        )
    except Exception as e:
        print("response_url post error:", e)


# ---------------- Routes ----------------
@app.route("/")
def home():
    return "StockBot is up. Use /price and /watchlist in Slack."


# ========= /price =========
def build_price_text(ticker: str) -> str:
    q = fetch_quote(ticker)
    if isinstance(q, dict) and "error" in q:
        return f"{ticker.upper()}: {q['error']}"
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

    # Try fast path so Slack doesn't time out
    q = fetch_quote(text, timeout=REQUEST_TIMEOUT)
    if isinstance(q, dict) and "error" in q:
        return jsonify(
            response_type="in_channel",
            text=f"{text.upper()}: {q['error']}",
        ), 200
    if q:
        price, change, pct = q
        return jsonify(
            response_type="in_channel",
            text=f"{text.upper()}: ${price:.2f} (Δ {change:.4f}, {pct})",
        ), 200

    # Fallback: acknowledge quickly then post public result via response_url
    threading.Thread(
        target=lambda: post_to_response_url(response_url, build_price_text(text)),
        daemon=True,
    ).start()
    return jsonify(response_type="ephemeral", text="Working…"), 200


# ========= /watchlist =========
def build_watchlist_text() -> str:
    if not watchlist:
        return "📭 Watchlist is empty."

    lines = []
    for t in sorted(watchlist):
        q = fetch_quote(t)
        if isinstance(q, dict) and "error" in q:
            lines.append(f"{t}: {q['error']}")
        elif q:
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
            text="Usage: `/watchlist add TICKER [TICKER…]`, `/watchlist remove TICKER [TICKER…]`, `/watchlist list`",
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
        return jsonify(response_type="in_channel", text=(". ".join(msg) or "No changes.")), 200

    if action == "list":
        # Listing can take a couple seconds; do a delayed public reply if needed.
        threading.Thread(
            target=lambda: post_to_response_url(response_url, build_watchlist_text()),
            daemon=True,
        ).start()
        return jsonify(response_type="ephemeral", text="Working…"), 200

    return jsonify(
        response_type="in_channel",
        text="Invalid command. Use: `/watchlist add TICKER [TICKER…]`, `/watchlist remove TICKER [TICKER…]`, `/watchlist list`",
    ), 200


# ---------------- Boot ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
