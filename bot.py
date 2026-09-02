import os
import time
import threading
from datetime import datetime

import requests
from flask import Flask, jsonify
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# =========================================================
# CONFIG
# =========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()

PORT = int(os.getenv("PORT", "10000"))

PAIRS = {
    "EURUSD=X": "EUR/USD",
    "GBPUSD=X": "GBP/USD",
    "USDJPY=X": "USD/JPY",
    "AUDUSD=X": "AUD/USD",
    "USDCAD=X": "USD/CAD",
    "NZDUSD=X": "NZD/USD",
    "EURJPY=X": "EUR/JPY",
    "GBPJPY=X": "GBP/JPY",
}

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

app = Flask(__name__)

LAST_UPDATE_ID = 0
BOT_STARTED = False


# =========================================================
# FLASK / RENDER HEALTH CHECK
# =========================================================

@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "bot": "running",
        "time": datetime.utcnow().isoformat()
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "healthy"
    })


# =========================================================
# TELEGRAM FUNCTIONS
# =========================================================

def telegram_request(method, data=None, files=None):
    """
    Safe Telegram API request.
    """

    if not TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_TOKEN is missing")
        return None

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"

    try:
        if files:
            response = requests.post(
                url,
                data=data,
                files=files,
                timeout=30
            )
        else:
            response = requests.post(
                url,
                json=data,
                timeout=30
            )

        return response.json()

    except Exception as error:
        print(f"Telegram error: {error}")
        return None


def send_message(chat_id, text):
    return telegram_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text
        }
    )


def send_photo(chat_id, photo_path, caption):
    try:
        with open(photo_path, "rb") as photo:
            return telegram_request(
                "sendPhoto",
                {
                    "chat_id": chat_id,
                    "caption": caption
                },
                {
                    "photo": photo
                }
            )
    except Exception as error:
        print(f"Photo error: {error}")
        return None


# =========================================================
# MARKET DATA
# =========================================================

def get_market_candles(symbol):
    """
    Get real market candles from Yahoo Finance.
    """

    url = YAHOO_URL.format(symbol=symbol)

    params = {
        "range": "1d",
        "interval": "5m"
    }

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        result = data.get("chart", {}).get("result")

        if not result:
            return []

        chart = result[0]

        timestamps = chart.get("timestamp", [])

        quote = chart.get("indicators", {}).get("quote", [{}])[0]

        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])

        candles = []

        length = min(
            len(timestamps),
            len(opens),
            len(highs),
            len(lows),
            len(closes)
        )

        for i in range(length):
            if (
                opens[i] is None
                or highs[i] is None
                or lows[i] is None
                or closes[i] is None
            ):
                continue

            candles.append({
                "time": timestamps[i],
                "open": float(opens[i]),
                "high": float(highs[i]),
                "low": float(lows[i]),
                "close": float(closes[i])
            })

        return candles

    except Exception as error:
        print(f"Market data error for {symbol}: {error}")
        return []


# =========================================================
# INDICATORS
# =========================================================

def calculate_ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    ema = sum(values[:period]) / period

    for value in values[period:]:
        ema = ((value - ema) * multiplier) + ema

    return ema


def calculate_rsi(values, period=14):
    if len(values) < period + 1:
        return 50.0

    gains = []
    losses = []

    for i in range(1, period + 1):
        change = values[-i] - values[-i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    average_gain = sum(gains) / period
    average_loss = sum(losses) / period

    if average_loss == 0:
        return 100.0

    rs = average_gain / average_loss

    rsi = 100 - (100 / (1 + rs))

    return rsi


def calculate_atr(candles, period=14):
    if len(candles) < period + 1:
        return 0

    ranges = []

    start_index = max(1, len(candles) - period)

    for i in range(start_index, len(candles)):
        current = candles[i]
        previous = candles[i - 1]

        true_range = max(
            current["high"] - current["low"],
            abs(current["high"] - previous["close"]),
            abs(current["low"] - previous["close"])
        )

        ranges.append(true_range)

    if not ranges:
        return 0

    return sum(ranges) / len(ranges)


# =========================================================
# SIGNAL ANALYSIS
# =========================================================

def analyze_pair(symbol, pair_name):
    candles = get_market_candles(symbol)

    if len(candles) < 30:
        return None

    closes = [candle["close"] for candle in candles]

    ema_9 = calculate_ema(closes, 9)
    ema_21 = calculate_ema(closes, 21)
    rsi = calculate_rsi(closes, 14)
    atr = calculate_atr(candles, 14)

    if ema_9 is None or ema_21 is None:
        return None

    last = candles[-1]
    previous = candles[-2]

    score_call = 0
    score_put = 0

    reasons_call = []
    reasons_put = []

    # EMA trend
    if ema_9 > ema_21:
        score_call += 30
        reasons_call.append("EMA bullish trend")
    else:
        score_put += 30
        reasons_put.append("EMA bearish trend")

    # RSI
    if rsi >= 52:
        score_call += 20
        reasons_call.append("RSI bullish")
    elif rsi <= 48:
        score_put += 20
        reasons_put.append("RSI bearish")

    # Last candle momentum
    if last["close"] > last["open"]:
        score_call += 20
        reasons_call.append("Last candle bullish")
    else:
        score_put += 20
        reasons_put.append("Last candle bearish")

    # Previous candle confirmation
    if previous["close"] > previous["open"]:
        score_call += 15
        reasons_call.append("Previous candle bullish")
    else:
        score_put += 15
        reasons_put.append("Previous candle bearish")

    # Price position
    if last["close"] > ema_9:
        score_call += 15
    else:
        score_put += 15

    if score_call >= score_put:
        direction = "CALL"
        score = score_call
        reasons = reasons_call
    else:
        direction = "PUT"
        score = score_put
        reasons = reasons_put

    confidence = min(99, max(50, score))

    return {
        "symbol": symbol,
        "pair": pair_name,
        "direction": direction,
        "score": score,
        "confidence": confidence,
        "rsi": round(rsi, 2),
        "atr": atr,
        "candles": candles[-30:],
        "reasons": reasons
    }


# =========================================================
# FIND ONE BEST PAIR
# =========================================================

def find_best_signal():
    signals = []

    for symbol, pair_name in PAIRS.items():
        print(f"Analyzing {pair_name}...")

        signal = analyze_pair(symbol, pair_name)

        if signal:
            signals.append(signal)

        time.sleep(1)

    if not signals:
        return None

    signals.sort(
        key=lambda item: item["score"],
        reverse=True
    )

    return signals[0]


# =========================================================
# CREATE CANDLE IMAGE
# =========================================================

def create_chart(signal):
    candles = signal["candles"]

    opens = [c["open"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]

    fig, ax = plt.subplots(figsize=(10, 6))

    for i in range(len(candles)):
        is_bullish = closes[i] >= opens[i]

        body_bottom = min(opens[i], closes[i])
        body_height = abs(closes[i] - opens[i])

        if body_height == 0:
            body_height = max(highs[i] - lows[i], 0.00001) * 0.05

        color = "green" if is_bullish else "red"

        ax.plot(
            [i, i],
            [lows[i], highs[i]],
            color=color,
            linewidth=1
        )

        ax.bar(
            i,
            body_height,
            bottom=body_bottom,
            width=0.6,
            color=color
        )

    last_close = closes[-1]

    if signal["direction"] == "CALL":
        projected_price = last_close + (signal["atr"] * 0.5)
        projected_color = "green"
    else:
        projected_price = last_close - (signal["atr"] * 0.5)
        projected_color = "red"

    projection_x = len(candles) + 1

    ax.scatter(
        projection_x,
        projected_price,
        s=150,
        color=projected_color,
        marker="^" if signal["direction"] == "CALL" else "v"
    )

    ax.axhline(
        projected_price,
        linestyle="--",
        linewidth=1
    )

    ax.set_title(
        f"{signal['pair']} | {signal['direction']} | Confidence {signal['confidence']}%"
    )

    ax.set_xlabel("Candles")
    ax.set_ylabel("Price")

    ax.grid(True, alpha=0.3)

    chart_path = "/tmp/signal_chart.png"

    plt.tight_layout()
    plt.savefig(chart_path, dpi=150)
    plt.close()

    return chart_path


# =========================================================
# SIGNAL COMMAND
# =========================================================

def process_signal(chat_id):
    send_message(
        chat_id,
        "🔎 Live market scan started...\nFinding ONE BEST pair..."
    )

    signal = find_best_signal()

    if not signal:
        send_message(
            chat_id,
            "❌ No valid market data found right now. Please try again later."
        )
        return

    chart_path = create_chart(signal)

    reasons_text = "\n".join(
        f"• {reason}"
        for reason in signal["reasons"][:4]
    )

    caption = (
        f"🏆 BEST SIGNAL\n\n"
        f"💱 Pair: {signal['pair']}\n"
        f"📈 Direction: {signal['direction']}\n"
        f"🎯 Confidence: {signal['confidence']}%\n"
        f"⭐ Score: {signal['score']}\n"
        f"⏱ Timeframe: 5 Minutes\n"
        f"📊 RSI: {signal['rsi']}\n\n"
        f"📌 Analysis:\n"
        f"{reasons_text}\n\n"
        f"⚠️ Educational signal only. Market risk exists."
    )

    send_photo(
        chat_id,
        chart_path,
        caption
    )

    try:
        if os.path.exists(chart_path):
            os.remove(chart_path)
    except Exception:
        pass


# =========================================================
# TELEGRAM UPDATE HANDLER
# =========================================================

def handle_update(update):
    message = update.get("message")

    if not message:
        return

    chat = message.get("chat", {})
    chat_id = chat.get("id")

    text = message.get("text", "")

    if not chat_id:
        return

    command = text.strip().lower()

    if command in ["/start", "start"]:
        send_message(
            chat_id,
            "🤖 Trading Signal Bot Online!\n\n"
            "Use /signal to scan live markets and get ONE BEST signal."
        )

    elif command in ["/signal", "signal", "/trade"]:
        process_signal(chat_id)

    else:
        send_message(
            chat_id,
            "Use:\n\n"
            "▶️ /signal - Find ONE BEST live market signal"
        )


# =========================================================
# TELEGRAM POLLING
# =========================================================

def telegram_bot_loop():
    global LAST_UPDATE_ID

    print("Telegram bot loop started")

    while True:
        try:
            if not TELEGRAM_TOKEN:
                print("Waiting for TELEGRAM_TOKEN...")
                time.sleep(10)
                continue

            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"

            params = {
                "timeout": 30,
                "offset": LAST_UPDATE_ID + 1
            }

            response = requests.get(
                url,
                params=params,
                timeout=40
            )

            data = response.json()

            if data.get("ok"):
                updates = data.get("result", [])

                for update in updates:
                    LAST_UPDATE_ID = update.get(
                        "update_id",
                        LAST_UPDATE_ID
                    )

                    try:
                        handle_update(update)
                    except Exception as error:
                        print(f"Update handling error: {error}")

        except Exception as error:
            print(f"Polling error: {error}")
            time.sleep(5)


# =========================================================
# START BOT
# =========================================================

def start_bot():
    global BOT_STARTED

    if BOT_STARTED:
        return

    BOT_STARTED = True

    thread = threading.Thread(
        target=telegram_bot_loop,
        daemon=True
    )

    thread.start()


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    start_bot()

    app.run(
        host="0.0.0.0",
        port=PORT
            )
