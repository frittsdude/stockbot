# main.py
import os
import threading
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

ALPHA_KEY = os.getenv("ALPHA_KEY", "FZTRK2YCAJQIWTM1")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

watchlist = set()

def slack_post(channel_id: str, text: str):
    if not SLACK_BOT_TOKEN:
        print("WARN: SLACK_BOT_TOKEN not set; cannot post to Slack.")
        return
    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {"channel": channel_id, "text": text}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        ok = r.ok and r.json().get("ok", False)
        if not ok:
            print("Slack API error:", r.status_code, r.text)
    except Exception as e:
        print("Slack post exception:", e)

def fetch_quote(ticker: str):
    t = ticker.strip().upper()
    url = (
        "https://www.alphavantage.co/query"
        f"?function=GLOBAL_QUOTE&symbol={t}&apikey={ALPHA_KEY}"
    )
    try:
        r = requests.get(url, timeout=15)
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

@app.route("/")
def home():
    return "StockBot is up. Use /price and /watchlist in Slack."

# ---------- /price ----------
def _do_price(channel_id: str, text: str):
    if not text:
        slack_post(channel_id, "Usage: `/price AAPL`")
        return
    quote = fetch_quote(text)
    if not quote:
        slack_post(channel_id, f"{text.upper()}: (no data)")
        return
    price, change, pct = quote
    slack_post(channel_id, f"{text.upper()}: ${price:.2f} (Δ {change:.4f}, {pct})")

@app.route("/slack/price", methods=["POST"])
def cmd_price():
    channel_id = request.form.get("channel_id", "")
    text = (request.form.get("text") or "").strip()
    # Run in background so we ack immediately
    threading.Thread(target=_do_price, args=(channel_id, text), daemon=True).start()
    # Ephemeral ack so you always see *something* right away
    return jsonify(response_type="ephemeral", text="Working…"), 200

# ---------- /watchlist ----------
def _do_watchlist(channel_id: str, parts):
    if not parts:
        slack_post(
            channel_id,
            "Usage: `/watchlist add TICKER [TICKER...]`, `/watchlist remove TICKER [TICKER...]`, `/watchlist list`",
        )
        return

    action = parts[0].lower()
    tickers = [p.upper() for p in parts[1:]] if len(parts) > 1 else []

    if action == "add" and tickers:
        for t in tickers:
            watchlist.add(t)
        slack_post(channel_id, f"✅ Added: {', '.join(tickers)}")
        return

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
        slack_post(channel_id, ". ".join(msg) or "No changes.")
        return

    if action == "list":
        if not watchlist:
            slack_post(channel_id, "📭 Watchlist is empty.")
            return
        lines = []
        for t in sorted(watchlist):
            q = fetch_quote(t)
            if q:
                price, change, pct = q
                lines.append(f"{t}: ${price:.2f} (Δ {change:.4f}, {pct})")
            else:
                lines.append(f"{t}: (no data)")
        slack_post(channel_id, "📊 Watchlist:\n" + "\n".join(lines))
        return

    slack_post(
        channel_id,
        "Invalid command. Use: `/watchlist add TICKER [TICKER...]`, `/watchlist remove TICKER [TICKER...]`, `/watchlist list`",
    )

@app.route("/slack/watchlist", methods=["POST"])
def cmd_watchlist():
    channel_id = request.form.get("channel_id", "")
    parts = (request.form.get("text") or "").strip().split()
    threading.Thread(target=_do_watchlist, args=(channel_id, parts), daemon=True).start()
    return jsonify(response_type="ephemeral", text="Working…"), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
