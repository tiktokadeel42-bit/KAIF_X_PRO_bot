import os
import time
import threading
import tempfile

import requests
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from flask import Flask, jsonify


# ============================================================
# CONFIGURATION
# ============================================================

BOT_NAME = "BEST SIGNAL BOT"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()

PORT = int(os.getenv("PORT", "10000"))

ANALYSIS_INTERVAL = 60
MIN_CONFIDENCE = 60

TIMEFRAME = "1m"

LIVE_PAIRS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT"
]

BINANCE_URL = "https://api.binance.com/api/v3/klines"


# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "bot": BOT_NAME
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "healthy"
    })


# ============================================================
# GLOBAL VARIABLES
# ============================================================

subscribers = set()

lock = threading.Lock()

services_started = False

last_sent_pair = None
last_sent_time = 0

telegram_offset = None


# ============================================================
# TELEGRAM HELPERS
# ============================================================

def telegram_url(method):
    return (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_TOKEN}/{method}"
    )


def send_message(chat_id, text):
    try:
        response = requests.post(
            telegram_url("sendMessage"),
            json={
                "chat_id": chat_id,
                "text": text
            },
            timeout=20
        )

        return response.json()

    except Exception as error:
        print("send_message error:", error)
        return {"ok": False}


def send_photo(chat_id, photo_path, caption):
    try:
        with open(photo_path, "rb") as photo:

            response = requests.post(
                telegram_url("sendPhoto"),
                data={
                    "chat_id": chat_id,
                    "caption": caption
                },
                files={
                    "photo": photo
                },
                timeout=30
            )

        return response.json()

    except Exception as error:
        print("send_photo error:", error)
        return {"ok": False}


def get_updates(offset=None):
    try:
        params = {
            "timeout": 25
        }

        if offset is not None:
            params["offset"] = offset

        response = requests.get(
            telegram_url("getUpdates"),
            params=params,
            timeout=35
        )

        return response.json()

    except Exception as error:
        print("get_updates error:", error)
        return {"ok": False, "result": []}


# ============================================================
# MARKET DATA
# ============================================================

def get_candles(symbol, limit=100):
    try:
        params = {
            "symbol": symbol,
            "interval": TIMEFRAME,
            "limit": limit
        }

        response = requests.get(
            BINANCE_URL,
            params=params,
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        candles = []

        for item in data:

            candle = {
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4])
            }

            candles.append(candle)

        return candles

    except Exception as error:
        print(f"Market data error for {symbol}:", error)
        return []


# ============================================================
# INDICATORS
# ============================================================

def calculate_ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    ema = sum(values[:period]) / period

    for value in values[period:]:
        ema = (
            value * multiplier
            + ema * (1 - multiplier)
        )

    return ema


def calculate_rsi(values, period=14):
    if len(values) <= period:
        return 50.0

    gains = []
    losses = []

    for index in range(1, period + 1):

        change = values[-index] - values[-index - 1]

        if change >= 0:
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

    return round(rsi, 2)


# ============================================================
# ANALYZE ONE PAIR
# ============================================================

def analyze_pair(symbol):

    candles = get_candles(symbol)

    if len(candles) < 30:
        return None

    closes = [
        candle["close"]
        for candle in candles
    ]

    ema_fast = calculate_ema(closes, 9)
    ema_slow = calculate_ema(closes, 21)

    if ema_fast is None or ema_slow is None:
        return None

    rsi = calculate_rsi(closes)

    last_candle = candles[-1]

    previous_candle = candles[-2]

    price = last_candle["close"]

    bullish_score = 0
    bearish_score = 0

    # EMA TREND
    if ema_fast > ema_slow:
        bullish_score += 30
    else:
        bearish_score += 30

    # RSI
    if rsi >= 50:
        bullish_score += 20
    else:
        bearish_score += 20

    # LAST CANDLE DIRECTION
    if last_candle["close"] > last_candle["open"]:
        bullish_score += 20
    else:
        bearish_score += 20

    # PREVIOUS CANDLE MOMENTUM
    if previous_candle["close"] > previous_candle["open"]:
        bullish_score += 15
    else:
        bearish_score += 15

    # PRICE VS EMA
    if price > ema_fast:
        bullish_score += 15
    else:
        bearish_score += 15

    if bullish_score >= bearish_score:

        direction = "CALL"
        score = bullish_score

    else:

        direction = "PUT"
        score = bearish_score

    score = min(score, 95)

    return {
        "pair": symbol,
        "direction": direction,
        "score": score,
        "price": price,
        "rsi": rsi,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "last_candle": last_candle,
        "previous_candle": previous_candle
    }


# ============================================================
# FIND ONLY ONE BEST PAIR
# ============================================================

def get_best_signal():

    all_signals = []

    print("Scanning pairs...")

    for pair in LIVE_PAIRS:

        result = analyze_pair(pair)

        if result is not None:

            all_signals.append(result)

        time.sleep(0.3)

    if not all_signals:
        return None

    all_signals.sort(
        key=lambda item: item["score"],
        reverse=True
    )

    best = all_signals[0]

    if best["score"] < MIN_CONFIDENCE:
        return None

    return best


# ============================================================
# CREATE 3 CANDLE IMAGE
# ============================================================

def draw_candle(ax, x, candle, width=0.55):

    open_price = candle["open"]
    close_price = candle["close"]
    high_price = candle["high"]
    low_price = candle["low"]

    bullish = close_price >= open_price

    body_bottom = min(open_price, close_price)
    body_top = max(open_price, close_price)

    ax.plot(
        [x, x],
        [low_price, high_price],
        linewidth=2
    )

    body_height = body_top - body_bottom

    if body_height == 0:
        body_height = abs(high_price - low_price) * 0.02

    rectangle = plt.Rectangle(
        (
            x - width / 2,
            body_bottom
        ),
        width,
        body_height,
        fill=True
    )

    ax.add_patch(rectangle)


def create_chart(signal):

    previous = signal["previous_candle"]
    last = signal["last_candle"]

    direction = signal["direction"]

    current_price = signal["price"]

    candle_range = (
        max(
            last["high"] - last["low"],
            previous["high"] - previous["low"]
        )
    )

    if candle_range <= 0:
        candle_range = current_price * 0.001

    if direction == "CALL":

        predicted = {
            "open": current_price,
            "close": current_price + candle_range * 0.7,
            "high": current_price + candle_range,
            "low": current_price - candle_range * 0.25
        }

    else:

        predicted = {
            "open": current_price,
            "close": current_price - candle_range * 0.7,
            "high": current_price + candle_range * 0.25,
            "low": current_price - candle_range
        }

    figure, axis = plt.subplots(
        figsize=(8, 5)
    )

    draw_candle(
        axis,
        0,
        previous
    )

    draw_candle(
        axis,
        1,
        last
    )

    draw_candle(
        axis,
        2,
        predicted
    )

    axis.set_xlim(-1, 3)

    minimum = min(
        previous["low"],
        last["low"],
        predicted["low"]
    )

    maximum = max(
        previous["high"],
        last["high"],
        predicted["high"]
    )

    padding = (maximum - minimum) * 0.15

    if padding == 0:
        padding = 1

    axis.set_ylim(
        minimum - padding,
        maximum + padding
    )

    axis.set_xticks([0, 1, 2])

    axis.set_xticklabels([
        "Previous",
        "Last",
        "Next Trade"
    ])

    axis.set_title(
        f"{signal['pair']} - {direction}"
    )

    axis.grid(True)

    file_path = os.path.join(
        tempfile.gettempdir(),
        f"signal_{int(time.time() * 1000)}.png"
    )

    figure.savefig(
        file_path,
        bbox_inches="tight",
        dpi=150
    )

    plt.close(figure)

    return file_path


# ============================================================
# SIGNAL TEXT
# ============================================================

def make_caption(signal):

    return (
        "🎯 BEST SINGLE SIGNAL\n\n"
        f"💱 Pair: {signal['pair']}\n"
        f"📈 Direction: {signal['direction']}\n"
        f"⭐ Confidence: {signal['score']}%\n"
        f"⏱ Timeframe: {TIMEFRAME}\n"
        f"💰 Price: {signal['price']}\n"
        f"📊 RSI: {signal['rsi']}\n\n"
        "⚠️ Signal is based on technical analysis. "
        "Trading involves risk."
    )


# ============================================================
# SEND SIGNAL TO ONE USER
# ============================================================

def send_best_signal(chat_id):

    signal = get_best_signal()

    if signal is None:

        send_message(
            chat_id,
            "⚠️ No strong signal found right now."
        )

        return

    image_path = None

    try:

        image_path = create_chart(signal)

        caption = make_caption(signal)

        send_photo(
            chat_id,
            image_path,
            caption
        )

    except Exception as error:

        print(
            "send_best_signal error:",
            error
        )

        send_message(
            chat_id,
            "⚠️ Error while creating signal."
        )

    finally:

        if (
            image_path
            and os.path.exists(image_path)
        ):
            try:
                os.remove(image_path)
            except Exception:
                pass


# ============================================================
# AUTOMATIC SIGNAL ENGINE
# ============================================================

def signal_engine():

    global last_sent_pair
    global last_sent_time

    while True:

        try:

            if not subscribers:

                time.sleep(10)
                continue

            signal = get_best_signal()

            if signal is None:

                time.sleep(ANALYSIS_INTERVAL)
                continue

            current_time = time.time()

            # Avoid sending same pair repeatedly too quickly
            if (
                signal["pair"] == last_sent_pair
                and current_time - last_sent_time
                < ANALYSIS_INTERVAL
            ):

                time.sleep(ANALYSIS_INTERVAL)
                continue

            image_path = None

            try:

                image_path = create_chart(signal)

                caption = make_caption(signal)

                for chat_id in list(subscribers):

                    result = send_photo(
                        chat_id,
                        image_path,
                        caption
                    )

                    if not result.get("ok"):
                        print(
                            "Failed to send to:",
                            chat_id
                        )

                last_sent_pair = signal["pair"]
                last_sent_time = current_time

                print(
                    "Best signal sent:",
                    signal["pair"],
                    signal["direction"],
                    signal["score"]
                )

            finally:

                if (
                    image_path
                    and os.path.exists(image_path)
                ):
                    try:
                        os.remove(image_path)
                    except Exception:
                        pass

            time.sleep(ANALYSIS_INTERVAL)

        except Exception as error:

            print(
                "Signal engine error:",
                error
            )

            time.sleep(15)


# ============================================================
# TELEGRAM COMMAND LISTENER
# ============================================================

def telegram_listener():

    global telegram_offset

    while True:

        try:

            data = get_updates(
                telegram_offset
            )

            if not data.get("ok"):
                time.sleep(5)
                continue

            updates = data.get(
                "result",
                []
            )

            for update in updates:

                telegram_offset = (
                    update["update_id"] + 1
                )

                message = update.get("message")

                if message is None:
                    continue

                chat = message.get("chat")

                if chat is None:
                    continue

                chat_id = chat["id"]

                command = (
                    message
                    .get("text", "")
                    .strip()
                    .lower()
                )

                if command == "/start":

                    subscribers.add(chat_id)

                    send_message(
                        chat_id,
                        "🤖 BEST SIGNAL BOT ACTIVATED\n\n"
                        "🏆 Bot scans all available pairs "
                        "and sends only ONE best signal.\n\n"
                        "Commands:\n"
                        "/signal - Get signal now\n"
                        "/status - Check bot\n"
                        "/stop - Stop signals"
                    )

                elif command == "/signal":

                    send_message(
                        chat_id,
                        "🔎 Scanning market for the best pair..."
                    )

                    send_best_signal(chat_id)

                elif command == "/status":

                    send_message(
                        chat_id,
                        "🟢 Bot Status: ONLINE\n"
                        f"📊 Pairs: {len(LIVE_PAIRS)}\n"
                        "🏆 Mode: ONE BEST SIGNAL"
                    )

                elif command == "/stop":

                    subscribers.discard(chat_id)

                    send_message(
                        chat_id,
                        "🛑 Automatic signals stopped."
                    )

        except Exception as error:

            print(
                "Telegram listener error:",
                error
            )

            time.sleep(10)


# ============================================================
# START BOT
# ============================================================

def start_bot():

    global services_started

    with lock:

        if services_started:
            return

        services_started = True

    if not TELEGRAM_TOKEN:

        print(
            "ERROR: TELEGRAM_TOKEN is missing."
        )

        return

    try:

        requests.post(
            telegram_url("deleteWebhook"),
            json={
                "drop_pending_updates": True
            },
            timeout=20
        )

    except Exception as error:

        print(
            "Webhook error:",
            error
        )

    listener_thread = threading.Thread(
        target=telegram_listener,
        daemon=True
    )

    signal_thread = threading.Thread(
        target=signal_engine,
        daemon=True
    )

    listener_thread.start()

    signal_thread.start()

    print("Bot started successfully.")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print(
        f"Starting {BOT_NAME}..."
    )

    start_bot()

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
)
