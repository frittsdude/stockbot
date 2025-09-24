from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# --- Simple in-memory watchlist ---
watchlist = []

# --- Dummy stock price fetcher (replace with real API) ---
def get_stock_price(ticker):
    # Example using Yahoo Finance API (or any other provider)
    # For now, we'll just return a fake number so it runs.
    return round(100 + hash(ticker) % 50 + 0.01 * (hash(ticker) % 100), 2)


# --- Slash command: /price TICKER ---
@app.route('/slack/price', methods=['POST'])
def price():
    ticker = request.form.get('text', '').strip().upper()
    if not ticker:
        return jsonify({
            "response_type": "ephemeral",
            "text": "⚠️ Please provide a ticker, e.g. `/price AAPL`"
        })

    price = get_stock_price(ticker)
    return jsonify({
        "response_type": "in_channel",   # visible to everyone
        "text": f"{ticker}: ${price}"
    })


# --- Slash command: /watchlist add/remove/show ---
@app.route('/slack/watchlist', methods=['POST'])
def watchlist_cmd():
    text = request.form.get('text', '').strip().split()
    if not text:
        return jsonify({
            "response_type": "ephemeral",
            "text": "⚠️ Usage: `/watchlist add TICKER`, `/watchlist remove TICKER`, or `/watchlist show`"
        })

    action = text[0].lower()

    if action == "add" and len(text) > 1:
        ticker = text[1].upper()
        if ticker not in watchlist:
            watchlist.append(ticker)
            return jsonify({
                "response_type": "in_channel",
                "text": f"✅ Added {ticker} to the watchlist."
            })
        else:
            return jsonify({
                "response_type": "ephemeral",
                "text": f"{ticker} is already on the watchlist."
            })

    elif action == "remove" and len(text) > 1:
        ticker = text[1].upper()
        if ticker in watchlist:
            watchlist.remove(ticker)
            return jsonify({
                "response_type": "in_channel",
                "text": f"🗑️ Removed {ticker} from the watchlist."
            })
        else:
            return jsonify({
                "response_type": "ephemeral",
                "text": f"{ticker} is not on the watchlist."
            })

    elif action == "show":
        if not watchlist:
            return jsonify({
                "response_type": "in_channel",
                "text": "📭 The watchlist is empty."
            })
        prices = [f"{t}: ${get_stock_price(t)}" for t in watchlist]
        return jsonify({
            "response_type": "in_channel",
            "text": "📈 Current Watchlist:\n" + "\n".join(prices)
        })

    else:
        return jsonify({
            "response_type": "ephemeral",
            "text": "⚠️ Invalid command. Try `/watchlist add TICKER`, `/watchlist remove TICKER`, or `/watchlist show`."
        })


# --- Home route just to confirm it's running ---
@app.route('/')
def home():
    return "StockBot is running!"


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
