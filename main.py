import requests
from flask import Flask, request, jsonify

ALPHA_KEY = "FZTRK2YCAJQIWTM1"
app = Flask(__name__)

# simple in-memory watchlist (per app; resets on restart)
watchlist = set()

# ---------- helpers ----------
def quote_line(ticker: str, timeout=2.5) -> str:
    try:
        r = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "GLOBAL_QUOTE", "symbol": ticker, "apikey": ALPHA_KEY},
            timeout=timeout,
        )
        q = r.json().get("Global Quote", {})
        px = q.get("05. price")
        if not px:
            return f"{ticker}: (no data)"
        chg = q.get("09. change", "—")
        pct = q.get("10. change percent", "—")
        return f"*{ticker}*: ${float(px):.2f}  (Δ {chg}, {pct})"
    except Exception:
        return f"{ticker}: (timeout/error)"

def render_quotes(tickers):
    tickers = [t.upper() for t in tickers][:8]
    lines = [quote_line(t) for t in tickers]
    return "\n".join(lines) if lines else "No valid tickers."

def public(text: str):
    """Slack public message (visible to channel)."""
    return jsonify({"response_type": "in_channel", "text": text})

def private(text: str):
    """Private (ephemeral) message to the user."""
    return jsonify({"response_type": "ephemeral", "text": text})

# ---------- routes ----------
@app.get("/")
def health():
    return "StockBot is up. Use /price or /watchlist in Slack."

@app.get("/slack/ping")
def ping():
    return "pong"

# /price AAPL TSLA
@app.route("/slack/price", methods=["POST", "GET"])
def price():
    # Allow manual GET testing via browser: .../slack/price?text=AAPL TSLA
    if request.method == "GET":
        text = (request.args.get("text") or "").strip()
        if not text:
            return "Usage: /price TICKER or /price AAPL TSLA"
        return render_quotes(text.split())

    # Slack POST
    text = (request.form.get("text") or "").strip()
    if not text:
        # keep usage hints private, everything else public
        return private("Usage: `/price TICKER` or `/price AAPL TSLA`")
    result = render_quotes(text.split())
    return public(result)

# /watchlist add TICKER | remove TICKER | list
@app.route("/slack/watchlist", methods=["POST", "GET"])
def handle_watchlist():
    # GET for quick browser tests: .../slack/watchlist?text=list
    if request.method == "GET":
        text = (request.args.get("text") or "").strip()
        return _watchlist_logic(text, public_for_get=False)

    # Slack POST → public
    text = (request.form.get("text") or "").strip()
    return _watchlist_logic(text, public_for_get=True)

def _watchlist_logic(text: str, public_for_get: bool):
    parts = text.split()
    if not parts:
        msg = "Usage: `/watchlist add TICKER`, `/watchlist remove TICKER`, `/watchlist list`"
        # In Slack POSTs we can return private usage, but you asked for all public:
        return public(msg) if public_for_get else msg

    cmd = parts[0].lower()

    if cmd == "add" and len(parts) > 1:
        for t in parts[1:]:
            watchlist.add(t.upper())
        msg = f"✅ Added: {' '.join([t.upper() for t in parts[1:]])}\nCurrent: {' '.join(sorted(watchlist)) or 'empty'}"
        return public(msg) if public_for_get else msg

    if cmd == "remove" and len(parts) > 1:
        for t in parts[1:]:
            watchlist.discard(t.upper())
        msg = f"🗑 Removed: {' '.join([t.upper() for t in parts[1:]])}\nCurrent: {' '.join(sorted(watchlist)) or 'empty'}"
        return public(msg) if public_for_get else msg

    if cmd == "list":
        if not watchlist:
            msg = "📭 Watchlist is empty. Add with `/watchlist add TICKER`"
            return public(msg) if public_for_get else msg
        msg = render_quotes(sorted(list(watchlist)))
        return public(msg) if public_for_get else msg

    # if user typed tickers without verb, treat as set/replace
    tickers = [p.upper() for p in parts if p.strip()]
    watchlist.clear()
    for t in tickers:
        watchlist.add(t)
    msg = f"✅ Watchlist set: {' '.join(sorted(watchlist)) or 'empty'}"
    return public(msg) if public_for_get else msg

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
