import os
import time
import threading
import requests
from datetime import datetime, timezone
from flask import Flask

# =========================
# SETTINGS
# =========================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_KEY")

# Add/remove pairs here
PAIRS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "USD/CAD",
    "USD/CHF",
    "NZD/USD",
    "EUR/JPY",
    "GBP/JPY"
]

INTERVAL = "1min"
EXPIRY_MINUTES = 1

# Minimum score required for a signal
MIN_SCORE = 4

# =========================
# TELEGRAM
# =========================

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

subscribers = set()
last_signals = {}


def telegram(method, data=None):
    try:
        url = f"{BASE_URL}/{method}"
        r = requests.post(url, json=data or {}, timeout=15)
        return r.json()
    except Exception as e:
        print("Telegram error:", e)
        return {}


def send_message(chat_id, text):
    return telegram(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
    )


# =========================
# MARKET DATA
# =========================

1000

        values = data["values"]

        # Oldest -> newest
        values.reverse()

        candles = []

        for x in values:
            candles.append({
                "open": float(x["open"]),
                "high": float(x["high"]),
                "low": float(x["low"]),
                "close": float(x["close"])
            })

        return candles

    except Exception as e:
        print("Candle error:", e)
        return []


# =========================
# INDICATORS
# =========================

def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = sum(values[:period]) / period

    for price in values[period:]:
        result = (price - result) * multiplier + result

    return result


def rsi(values, period=14):
    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, period + 1):
        change = values[i] - values[i - 1]

        if change >= 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]

        gain = max(change, 0)
        loss = max(-change, 0)

        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def bollinger(values, period=20, deviation=2):
    if len(values) < period:
        return None, None, None

    recent = values[-period:]

    middle = sum(recent) / period

    variance = sum(
        (x - middle) ** 2 for x in recent
    ) / period

    std = variance ** 0.5

    upper = middle + deviation * std
    lower = middle - deviation * std

    return upper, middle, lower


def macd(values):
    if len(values) < 35:
        return None, None

    ema12 = ema(values, 12)
    ema26 = ema(values, 26)

    if ema12 is None or ema26 is None:
        return None, None

    macd_line = ema12 - ema26

    # Simplified signal calculation
    macd_values = []

    for i in range(26, len(values)):
        e12 = ema(values[:i + 1], 12)
        e26 = ema(values[:i + 1], 26)

        if e12 is not None and e26 is not None:
            macd_values.append(e12 - e26)

    if len(macd_values) < 9:
        return macd_line, None

    signal = ema(macd_values, 9)

    return macd_line, signal


# =========================
# SIGNAL ENGINE
# =========================

def analyze(symbol):

    candles = get_candles(symbol)

    if len(candles) < 40:
        return None

    closes = [x["close"] for x in candles]

    price = closes[-1]

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)

    rsi_value = rsi(closes, 14)

    upper, middle, lower = bollinger(closes, 20, 2)

    macd_line, macd_signal = macd(closes)

    if None in [
        ema9,
        ema21,
        rsi_value,
        upper,
        middle,
        lower,
        macd_line,
        macd_signal
    ]:
        return None

    call_score = 0
    put_score = 0

    reasons_call = []
    reasons_put = []

    # EMA TREND
    if ema9 > ema21:
        call_score += 1
        reasons_call.append("EMA bullish")

    elif ema9 < ema21:
        put_score += 1
        reasons_put.append("EMA bearish")

    # RSI
    if 50 < rsi_value < 70:
        call_score += 1
        reasons_call.append("RSI bullish")

    elif 30 < rsi_value < 50:
        put_score += 1
        reasons_put.append("RSI bearish")

    # MACD
    if macd_line > macd_signal:
        call_score += 1
        reasons_call.append("MACD bullish")

    elif macd_line < macd_signal:
        put_score += 1
        reasons_put.append("MACD bearish")

    # Bollinger
    if price > middle:
        call_score += 1
        reasons_call.append("Price above BB middle")

    elif price < middle:
        put_score += 1
        reasons_put.append("Price below BB middle")

    # Recent candle momentum
    if closes[-1] > closes[-2]:
        call_score += 1
        reasons_call.append("Bullish momentum")

    elif closes[-1] < closes[-2]:
        put_score += 1
        reasons_put.append("Bearish momentum")

    total = call_score + put_score

    if total == 0:
        return None

    if call_score > put_score and call_score >= MIN_SCORE:
        direction = "CALL"
        score = call_score
        reasons = reasons_call

    elif put_score > call_score and put_score >= MIN_SCORE:
        direction = "PUT"
        score = put_score
        reasons = reasons_put

    else:
        return None

    confidence = int(
        min(95, 55 + (score - MIN_SCORE) * 8)
    )

    return {
        "pair": symbol,
        "direction": direction,
        "confidence": confidence,
        "price": price,
        "rsi": rsi_value,
        "ema9": ema9,
        "ema21": ema21,
        "macd": macd_line,
        "score": score,
        "reasons": reasons
    }


# =========================
# FORMAT SIGNAL
# =========================

def format_signal(signal):

    direction = signal["direction"]

    if direction == "CALL":
        emoji = "🟢"
        action = "CALL / UP"
    else:
        emoji = "🔴"
        action = "PUT / DOWN"

    reasons = "\n".join(
        f"• {x}" for x in signal["reasons"]
    )

    now = datetime.now().strftime("%H:%M:%S")

    text = f"""
<b>👑 KAIF X PRO</b>

{emoji} <b>{action}</b>

💱 Pair: <b>{signal["pair"]}</b>
⏱ Timeframe: <b>1 Minute</b>
⌛ Expiry: <b>1 Minute</b>

🎯 Confidence: <b>{signal["confidence"]}%</b>

💰 Price: <b>{signal["price"]:.5f}</b>
📊 RSI: <b>{signal["rsi"]:.2f}</b>

📈 EMA 9: <b>{signal["ema9"]:.5f}</b>
📉 EMA 21: <b>{signal["ema21"]:.5f}</b>

🧠 Score: <b>{signal["score"]}/5</b>

<b>Analysis:</b>
{reasons}

🕐 Signal Time: <b>{now}</b>

⚠️ <i>For analysis only. No signal can guarantee a win.</i>
"""

    return text


# =========================
# SIGNAL LOOP
# =========================

def signal_loop():

    print("Signal engine started...")

    while True:

        try:

            if len(subscribers) == 0:
                time.sleep(5)
                continue

            for pair in PAIRS:

                signal = analyze(pair)

                if signal is None:
                    continue

                # Prevent duplicate signal
                signal_key = (
                    pair,
                    signal["direction"],
                    round(signal["price"], 5)
                )

                if last_signals.get(pair) == signal_key:
                    continue

                last_signals[pair] = signal_key

                message = format_signal(signal)

                for chat_id in list(subscribers):
                    send_message(chat_id, message)

                print(
                    pair,
                    signal["direction"],
                    signal["confidence"]
                )

                time.sleep(2)

            # Around once per minute
            time.sleep(45)

        except Exception as e:

            print("Signal loop error:", e)

            time.sleep(10)


# =========================
# TELEGRAM COMMANDS
# =========================

def handle_updates():

    offset = None

    while True:

        try:

            params = {
                "timeout": 25
            }

            if offset:
                params["offset"] = offset

            r = requests.get(
                f"{BASE_URL}/getUpdates",
                params=params,
                timeout=35
            )

            data = r.json()

            for update in data.get("result", []):

                offset = update["update_id"] + 1

                message = update.get("message")

                if not message:
                    continue

                chat_id = message["chat"]["id"]

                text = message.get("text", "").strip()

                if text == "/start":

                    subscribers.add(chat_id)

                    send_message(
                        chat_id,
                        """
<b>👑 KAIF X PRO</b>

✅ Signal bot activated.

📊 Automatic analysis is ON.

⏱ Timeframe: 1 Minute
⌛ Expiry: 1 Minute

Commands:

/start - Start signals
/stop - Stop signals
/signal - Get current analysis
/pairs - Show pairs
"""
                    )

                elif text == "/stop":

                    subscribers.discard(chat_id)

                    send_message(
                        chat_id,
                        "🛑 Automatic signals stopped."
                    )

                elif text == "/pairs":

                    pair_text = "\n".join(
                        f"• {p}" for p in PAIRS
                    )

                    send_message(
                        chat_id,
                        f"<b>📊 Available Pairs</b>\n\n{pair_text}"
                    )

                elif text == "/signal":

                    send_message(
                        chat_id,
                        "🔎 Analyzing market... Please wait."
                    )

                    sent = False

                    for pair in PAIRS:

                        signal = analyze(pair)

                        if signal:

                            send_message(
                                chat_id,
                                format_signal(signal)
                            )

                            sent = True
                            break

                    if not sent:

                        send_message(
                            chat_id,
                            "⚠️ No strong signal right now. Please wait."
                        )

        except Exception as e:

            print("Update error:", e)

            time.sleep(5)


# =========================
# RENDER WEB SERVER
# =========================

app = Flask(__name__)


@app.route("/")
def home():
    return "KAIF X PRO Telegram Signal Bot is running."


@app.route("/health")
def health():
    return "OK"


# =========================
# START
# =========================

if __name__ == "__main__":

    if not TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_TOKEN is missing.")

    if not TWELVE_DATA_KEY:
        print("ERROR: TWELVE_DATA_KEY is missing.")

    # Telegram listener
    threading.Thread(
        target=handle_updates,
        daemon=True
    ).start()

    # Signal engine
    threading.Thread(
        target=signal_loop,
        daemon=True
    ).start()

    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
  )
