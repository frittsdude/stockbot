import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# Get Alpha Vantage key from environment, or use fallback
ALPHA_KEY = os.getenv("ALPHA_KEY", "FZTRK2YCAJQIWTM1")

# In-memory watchlist (simple demo, clears if app restarts)
watchlist = set()


@app.route("/")
def home():
    return "StockBot is up. Use /price in Slack."


@app.route("/slack/price", methods=["POST"])
def price():
    text = request.form.get("text", "").strip().upper()
    if not text:
        return jsonify(
            response_type="ephemeral",
            text="Please provide a ticker, e.g. `/price AAPL`"
        )

    url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={text}&apikey={ALPHA_KEY}"
    r = requests.get(url)
    data = r.json().get("Global Quote", {})

    if "05. price" not in data:
        return jsonify(response_type="ephemeral", text=f"{text}: (no data)")

    price = float(data["05. price"])
    change = float(data["09. change"])
    change_percent = data["10. change percent"]

    return jsonify(
        response_type="in_channel",  # visible to everyone
        text=f"{text}: ${price:.2f} (Δ {change:.4f}, {change_percent})"
    )


@app.route("/slack/watchlist", methods=["POST"])
def manage_watchlist():
    text = request.form.get("text", "").strip().upper()
    parts = text.split()

    if not parts:
        return jsonify(
            response_type="ephemeral",
            text="Usage: `/watchlist add TICKER`, `/watchlist remove TICKER`, `/watchlist list`"
        )

    cmd = parts[0]

    if cmd == "ADD" and len(parts) > 1:
        ticker = parts[1]
        watchlist.add(ticker)
        return jsonify(
            response_type="in_channel",
            text=f"✅ Added {ticker} to the watchlist."
        )

    elif cmd == "REMOVE" and len(parts) > 1:
        ticker = parts[1]
        if ticker in watchlist:
            watchlist.remove(ticker)
            return jsonify(
                response_type="in_channel",
                text=f"❌ Removed {ticker} from the watchlist."
            )
        else:
            return jsonify(
                response_type="ephemeral",
                text=f"{ticker} was not in the watchlist."
            )

    elif cmd == "LIST":
        if not watchlist:
            return jsonify(
                response_type="ephemeral",
                text="Watchlist is empty."
            )
        return jsonify(
            response_type="in_channel",
            text="📊 Watchlist: " + ", ".join(sorted(watchlist))
        )

    else:
        return jsonify(
            response_type="ephemeral",
            text="Invalid command. Use `/watchlist add TICKER`, `/watchlist remove TICKER`, or `/watchlist list`."
        )


if __name__ == "__main__":
    # Render assigns PORT automatically
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 3000)))
