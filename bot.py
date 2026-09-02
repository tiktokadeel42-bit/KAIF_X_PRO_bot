import os
import time
import threading
import tempfile

import requests
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from flask import Flask, jsonify


# ============================================================
# CONFIGURATION
# ============================================================

BOT_NAME = "KAIFX_PRO_BOT"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()

PORT = int(os.getenv("PORT", "10000"))

TIMEFRAME = "1m"

ANALYSIS_INTERVAL = 60

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
        "bot": BOT_NAME,
        "mode": "ONE BEST SIGNAL"
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

telegram_offset = None

services_started = False

start_lock = threading.Lock()


# ============================================================
# TELEGRAM FUNCTIONS
# ============================================================

def telegram_url(method):
    return f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"


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
        print("MESSAGE ERROR:", error)

        return {
            "ok": False
        }


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
        print("PHOTO ERROR:", error)

        return {
            "ok": False
        }


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
        print("UPDATE ERROR:", error)

        return {
            "ok": False,
            "result": []
        }


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

            candles.append({
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4])
            })

        return candles

    except Exception as error:

        print(f"MARKET ERROR {symbol}:", error)

        return []


# ============================================================
# EMA
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


# ============================================================
# RSI
# ============================================================

def calculate_rsi(values, period=14):

    if len(values) < period + 1:
        return 50.0

    gains = []
    losses = []

    start = len(values) - period

    for index in range(start, len(values)):

        change = values[index] - values[index - 1]

        if change >= 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    average_gain = sum(gains) / period
    average_loss = sum(losses) / period

    if average_loss == 0:

        if average_gain == 0:
            return 50.0

        return 100.0

    rs = average_gain / average_loss

    rsi = 100 - (100 / (1 + rs))

    return round(rsi, 2)


# ============================================================
# CANDLE DIRECTION
# ============================================================

def is_bullish(candle):
    return candle["close"] >= candle["open"]


def candle_strength(candle):

    candle_range = candle["high"] - candle["low"]

    if candle_range <= 0:
        return 0

    body = abs(
        candle["close"] - candle["open"]
    )

    return body / candle_range


# ============================================================
# ANALYZE PAIR
# ============================================================

def analyze_pair(symbol):

    candles = get_candles(symbol)

    if len(candles) < 30:
        return None

    closes = []

    for candle in candles:
        closes.append(candle["close"])

    ema_fast = calculate_ema(closes, 9)
    ema_slow = calculate_ema(closes, 21)

    if ema_fast is None or ema_slow is None:
        return None

    rsi = calculate_rsi(closes)

    previous = candles[-2]
    last = candles[-1]

    price = last["close"]

    bullish_score = 0
    bearish_score = 0


    # EMA TREND
    if ema_fast > ema_slow:
        bullish_score += 25
    else:
        bearish_score += 25


    # PRICE POSITION
    if price >= ema_fast:
        bullish_score += 20
    else:
        bearish_score += 20


    # RSI
    if rsi >= 50:
        bullish_score += 20
    else:
        bearish_score += 20


    # LAST CANDLE
    if is_bullish(last):
        bullish_score += 20
    else:
        bearish_score += 20


    # PREVIOUS CANDLE
    if is_bullish(previous):
        bullish_score += 15
    else:
        bearish_score += 15


    if bullish_score >= bearish_score:

        direction = "CALL"
        confidence = bullish_score

    else:

        direction = "PUT"
        confidence = bearish_score


    # Confidence is based on the rule score.
    # The best pair is always returned.
    confidence = max(50, min(confidence, 95))


    return {
        "pair": symbol,
        "direction": direction,
        "score": confidence,
        "price": price,
        "rsi": rsi,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "previous": previous,
        "last": last,
        "strength": candle_strength(last)
    }


# ============================================================
# FIND ONLY ONE BEST PAIR
# ============================================================

def get_best_signal():

    signals = []

    print("SCANNING MARKET...")

    for pair in LIVE_PAIRS:

        try:

            result = analyze_pair(pair)

            if result is not None:
                signals.append(result)

        except Exception as error:

            print(f"ANALYSIS ERROR {pair}:", error)

        time.sleep(0.3)


    if not signals:
        return None


    signals.sort(
        key=lambda item: item["score"],
        reverse=True
    )


    best_signal = signals[0]


    print(
        "BEST PAIR:",
        best_signal["pair"],
        best_signal["direction"],
        best_signal["score"]
    )


    return best_signal


# ============================================================
# DRAW ONE CANDLE
# ============================================================

def draw_candle(axis, x, candle, bullish=True):

    open_price = candle["open"]
    close_price = candle["close"]
    high_price = candle["high"]
    low_price = candle["low"]

    if bullish:
        color = "green"
    else:
        color = "red"


    axis.plot(
        [x, x],
        [low_price, high_price],
        color=color,
        linewidth=2
    )


    body_bottom = min(
        open_price,
        close_price
    )

    body_top = max(
        open_price,
        close_price
    )

    body_height = body_top - body_bottom


    if body_height == 0:

        total_range = high_price - low_price

        if total_range == 0:
            total_range = 1

        body_height = total_range * 0.03


    rectangle = Rectangle(
        (
            x - 0.3,
            body_bottom
        ),
        0.6,
        body_height,
        facecolor=color,
        edgecolor=color
    )


    axis.add_patch(rectangle)


# ============================================================
# CREATE NEXT CANDLE PREDICTION
# ============================================================

def create_predicted_candle(signal):

    last = signal["last"]

    current_price = last["close"]

    candle_range = last["high"] - last["low"]


    if candle_range <= 0:
        candle_range = current_price * 0.001


    direction = signal["direction"]


    if direction == "CALL":

        predicted = {
            "open": current_price,
            "close": current_price + (candle_range * 0.70),
            "high": current_price + candle_range,
            "low": current_price - (candle_range * 0.20)
        }

    else:

        predicted = {
            "open": current_price,
            "close": current_price - (candle_range * 0.70),
            "high": current_price + (candle_range * 0.20),
            "low": current_price - candle_range
        }


    return predicted


# ============================================================
# CREATE 3 CANDLE IMAGE
# ============================================================

def create_chart(signal):

    previous = signal["previous"]
    last = signal["last"]

    predicted = create_predicted_candle(signal)


    figure, axis = plt.subplots(
        figsize=(8, 6)
    )


    draw_candle(
        axis,
        0,
        previous,
        is_bullish(previous)
    )


    draw_candle(
        axis,
        1,
        last,
        is_bullish(last)
    )


    predicted_bullish = (
        predicted["close"]
        >=
        predicted["open"]
    )


    draw_candle(
        axis,
        2,
        predicted,
        predicted_bullish
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


    padding = (
        maximum - minimum
    ) * 0.15


    if padding <= 0:
        padding = 1


    axis.set_ylim(
        minimum - padding,
        maximum + padding
    )


    axis.set_xticks([
        0,
        1,
        2
    ])


    axis.set_xticklabels([
        "Previous Candle",
        "Last Candle",
        "Next Candle"
    ])


    axis.set_title(
        "Market Candle Structure"
    )


    axis.grid(
        True,
        alpha=0.3
    )


    file_name = (
        f"signal_{int(time.time() * 1000)}.png"
    )


    file_path = os.path.join(
        tempfile.gettempdir(),
        file_name
    )


    figure.savefig(
        file_path,
        dpi=150,
        bbox_inches="tight"
    )


    plt.close(figure)


    return file_path


# ============================================================
# SIGNAL CAPTION
# ============================================================

def make_caption(signal):

    text = (
        "🏆 BEST SINGLE SIGNAL\n\n"
        f"💱 Pair: {signal['pair']}\n"
        f"📈 Direction: {signal['direction']}\n"
        f"⭐ Confidence Score: {signal['score']}%\n"
        f"⏱ Timeframe: {TIMEFRAME}\n"
        f"💰 Current Price: {signal['price']}\n"
        f"📊 RSI: {signal['rsi']}\n\n"
        "🕯 Chart:\n"
        "1️⃣ Previous Candle\n"
        "2️⃣ Last Market Candle\n"
        "3️⃣ Estimated Next Candle Structure\n\n"
        "⚠️ Technical analysis only. "
        "No trade is guaranteed."
    )

    return text


# ============================================================
# SEND ONE SIGNAL
# ============================================================

def send_best_signal(chat_id):

    signal = get_best_signal()


    if signal is None:

        send_message(
            chat_id,
            "⚠️ Market data is temporarily unavailable. Please try again."
        )

        return


    image_path = None


    try:

        image_path = create_chart(signal)

        caption = make_caption(signal)

        result = send_photo(
            chat_id,
            image_path,
            caption
        )


        if not result.get("ok"):

            send_message(
                chat_id,
                caption
            )


    except Exception as error:

        print("SEND SIGNAL ERROR:", error)


        send_message(
            chat_id,
            "⚠️ Signal processing error. Please try again."
        )


    finally:

        if image_path and os.path.exists(image_path):

            try:
                os.remove(image_path)

            except Exception:
                pass


# ============================================================
# AUTOMATIC SIGNAL ENGINE
# ============================================================

def signal_engine():

    while True:

        try:

            if not subscribers:

                time.sleep(10)
                continue


            signal = get_best_signal()


            if signal is None:

                time.sleep(ANALYSIS_INTERVAL)
                continue


            image_path = None


            try:

                image_path = create_chart(signal)

                caption = make_caption(signal)


                for chat_id in list(subscribers):

                    try:

                        send_photo(
                            chat_id,
                            image_path,
                            caption
                        )

                    except Exception as error:

                        print(
                            "AUTO SEND ERROR:",
                            error
                        )


                print(
                    "AUTO SIGNAL SENT:",
                    signal["pair"]
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
                "SIGNAL ENGINE ERROR:",
                error
            )

            time.sleep(15)


# ============================================================
# TELEGRAM LISTENER
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


                # START
                if command == "/start":

                    subscribers.add(chat_id)


                    send_message(
                        chat_id,
                        "🤖 KAIFX_PRO_BOT ACTIVATED\n\n"
                        "🏆 Bot scans all available pairs "
                        "and selects ONLY ONE best signal.\n\n"
                        "Commands:\n"
                        "/signal - Get best signal now\n"
                        "/status - Bot status\n"
                        "/stop - Stop automatic signals"
                    )


                # SIGNAL
                elif command == "/signal":

                    send_message(
                        chat_id,
                        "🔎 Scanning market for the best pair..."
                    )


                    send_best_signal(chat_id)


                # STATUS
                elif command == "/status":

                    send_message(
                        chat_id,
                        "🟢 Bot Status: ONLINE\n"
                        f"📊 Pairs Scanned: {len(LIVE_PAIRS)}\n"
                        "🏆 Mode: ONE BEST SIGNAL\n"
                        f"⏱ Timeframe: {TIMEFRAME}"
                    )


                # STOP
                elif command == "/stop":

                    subscribers.discard(chat_id)


                    send_message(
                        chat_id,
                        "🛑 Automatic signals stopped.\n"
                        "Use /start to activate again."
                    )


        except Exception as error:

            print(
                "LISTENER ERROR:",
                error
            )

            time.sleep(10)


# ============================================================
# START BOT
# ============================================================

def start_bot():

    global services_started


    with start_lock:

        if services_started:
            return

        services_started = True


    if not TELEGRAM_TOKEN:

        print(
            "ERROR: TELEGRAM_TOKEN environment variable is missing."
        )

        return


    # Remove webhook for polling mode
    try:

        requests.post(
            telegram_url("deleteWebhook"),
            json={
                "drop_pending_updates": False
            },
            timeout=20
        )

    except Exception as error:

        print(
            "WEBHOOK ERROR:",
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


    print("====================================")
    print("BOT STARTED SUCCESSFULLY")
    print(BOT_NAME)
    print("MODE: ONE BEST SIGNAL")
    print("====================================")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print("Starting bot...")

    start_bot()


    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )
