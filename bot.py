import os
import time
import threading
import logging
from io import BytesIO
from datetime import datetime

import requests
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from flask import Flask

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "10000"))

TIMEFRAME = "1m"
SCAN_INTERVAL = 60
AUTO_SCAN_INTERVAL = 60
SIGNAL_COOLDOWN = 300

MIN_CONFIDENCE = 58

BINANCE_URL = "https://api.binance.com/api/v3/klines"
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Live market pairs
PAIRS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "TRXUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "MATICUSDT",
]

BOT_NAME = "KAIFX_PRO_BOT"

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)

# =========================================================
# FLASK / RENDER HEALTH SERVER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return {
        "status": "online",
        "bot": BOT_NAME,
        "mode": "ONE BEST SIGNAL"
    }


@app.route("/health")
def health():
    return {
        "status": "healthy"
    }


# =========================================================
# GLOBAL DATA
# =========================================================

subscribers = set()

last_update_id = None
last_signal_time = 0

state_lock = threading.Lock()


# =========================================================
# TELEGRAM FUNCTIONS
# =========================================================

def telegram_request(method, data=None, files=None):

    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is missing!")
        return None

    url = f"{TELEGRAM_API}/{method}"

    try:

        response = requests.post(
            url,
            data=data,
            files=files,
            timeout=30
        )

        return response.json()

    except Exception as error:

        logger.exception(
            "Telegram request error: %s",
            error
        )

        return None


def send_message(chat_id, text):

    data = {
        "chat_id": str(chat_id),
        "text": text
    }

    return telegram_request(
        "sendMessage",
        data=data
    )


def send_photo(chat_id, photo_bytes, caption):

    files = {
        "photo": (
            "signal.png",
            photo_bytes,
            "image/png"
        )
    }

    data = {
        "chat_id": str(chat_id),
        "caption": caption
    }

    return telegram_request(
        "sendPhoto",
        data=data,
        files=files
    )


# =========================================================
# MARKET DATA
# =========================================================

def get_candles(symbol, interval=TIMEFRAME, limit=100):

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    try:

        response = requests.get(
            BINANCE_URL,
            params=params,
            timeout=15
        )

        response.raise_for_status()

        raw_data = response.json()

        candles = []

        for item in raw_data:

            candles.append({
                "open_time": int(item[0]),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
                "close_time": int(item[6])
            })

        return candles

    except Exception as error:

        logger.warning(
            "Market error for %s: %s",
            symbol,
            error
        )

        return []


# =========================================================
# INDICATORS
# =========================================================

def calculate_ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    ema = sum(values[:period]) / period

    for price in values[period:]:

        ema = (
            price * multiplier
            + ema * (1 - multiplier)
        )

    return ema


def calculate_rsi(values, period=14):

    if len(values) <= period:
        return 50.0

    gains = []
    losses = []

    for index in range(1, len(values)):

        difference = values[index] - values[index - 1]

        if difference >= 0:
            gains.append(difference)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(difference))

    average_gain = sum(gains[-period:]) / period
    average_loss = sum(losses[-period:]) / period

    if average_loss == 0:
        return 100.0

    rs = average_gain / average_loss

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi


# =========================================================
# CANDLE ANALYSIS
# =========================================================

def candle_direction(candle):

    if candle["close"] > candle["open"]:
        return "BULLISH"

    if candle["close"] < candle["open"]:
        return "BEARISH"

    return "NEUTRAL"


def candle_strength(candle):

    body = abs(
        candle["close"] - candle["open"]
    )

    total_range = max(
        candle["high"] - candle["low"],
        0.00000001
    )

    return body / total_range


def analyze_pair(symbol):

    candles = get_candles(symbol)

    if len(candles) < 30:
        return None

    # Last candle can still be forming.
    # We use closed candles for analysis.
    closed = candles[:-1]

    if len(closed) < 25:
        return None

    closes = [
        candle["close"]
        for candle in closed
    ]

    last = closed[-1]
    previous = closed[-2]

    ema_fast = calculate_ema(
        closes[-30:],
        9
    )

    ema_slow = calculate_ema(
        closes[-30:],
        21
    )

    rsi = calculate_rsi(
        closes,
        14
    )

    last_direction = candle_direction(last)
    previous_direction = candle_direction(previous)

    last_strength = candle_strength(last)
    previous_strength = candle_strength(previous)

    bullish_score = 0
    bearish_score = 0

    reasons_bull = []
    reasons_bear = []

    # EMA TREND
    if (
        ema_fast is not None
        and ema_slow is not None
    ):

        if ema_fast > ema_slow:

            bullish_score += 25

            reasons_bull.append(
                "EMA trend bullish"
            )

        elif ema_fast < ema_slow:

            bearish_score += 25

            reasons_bear.append(
                "EMA trend bearish"
            )

    # RSI
    if 50 < rsi < 75:

        bullish_score += 15

        reasons_bull.append(
            f"RSI bullish ({rsi:.1f})"
        )

    elif 25 < rsi < 50:

        bearish_score += 15

        reasons_bear.append(
            f"RSI bearish ({rsi:.1f})"
        )

    # LAST CANDLE
    if last_direction == "BULLISH":

        bullish_score += 20

        if last_strength > 0.5:

            bullish_score += 10

        reasons_bull.append(
            "Strong bullish candle"
        )

    elif last_direction == "BEARISH":

        bearish_score += 20

        if last_strength > 0.5:

            bearish_score += 10

        reasons_bear.append(
            "Strong bearish candle"
        )

    # TWO CANDLE CONFIRMATION
    if (
        last_direction == "BULLISH"
        and previous_direction == "BULLISH"
    ):

        bullish_score += 20

        reasons_bull.append(
            "Two candle bullish confirmation"
        )

    if (
        last_direction == "BEARISH"
        and previous_direction == "BEARISH"
    ):

        bearish_score += 20

        reasons_bear.append(
            "Two candle bearish confirmation"
        )

    # MOMENTUM
    if last["close"] > previous["close"]:

        bullish_score += 10

    elif last["close"] < previous["close"]:

        bearish_score += 10

    # Select direction
    if bullish_score > bearish_score:

        direction = "CALL"
        score = bullish_score
        reasons = reasons_bull

    elif bearish_score > bullish_score:

        direction = "PUT"
        score = bearish_score
        reasons = reasons_bear

    else:

        return None

    confidence = min(
        int(score),
        95
    )

    return {
        "symbol": symbol,
        "market": "LIVE",
        "direction": direction,
        "confidence": confidence,
        "score": score,
        "rsi": round(rsi, 2),
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "last": last,
        "previous": previous,
        "reasons": reasons[:3],
        "timeframe": TIMEFRAME
    }


# =========================================================
# FIND BEST SINGLE SIGNAL
# =========================================================

def find_best_signal():

    logger.info(
        "Scanning %s live market pairs...",
        len(PAIRS)
    )

    results = []

    for symbol in PAIRS:

        try:

            result = analyze_pair(symbol)

            if result is not None:

                results.append(result)

            time.sleep(0.15)

        except Exception as error:

            logger.warning(
                "Analysis failed for %s: %s",
                symbol,
                error
            )

    if not results:

        return None

    results.sort(
        key=lambda item: item["confidence"],
        reverse=True
    )

    best = results[0]

    logger.info(
        "Best pair: %s | %s | %s%%",
        best["symbol"],
        best["direction"],
        best["confidence"]
    )

    return best


# =========================================================
# CREATE 3 CANDLE IMAGE
# =========================================================

def draw_candle(ax, x, candle, label=None):

    opening = candle["open"]
    closing = candle["close"]
    high = candle["high"]
    low = candle["low"]

    bullish = closing >= opening

    color = "green" if bullish else "red"

    # Wick
    ax.plot(
        [x, x],
        [low, high],
        color="black",
        linewidth=1.5
    )

    body_bottom = min(
        opening,
        closing
    )

    body_height = abs(
        closing - opening
    )

    if body_height == 0:

        body_height = (
            high - low
        ) * 0.03

    rectangle = plt.Rectangle(
        (
            x - 0.25,
            body_bottom
        ),
        0.5,
        body_height,
        facecolor=color,
        edgecolor="black",
        linewidth=1
    )

    ax.add_patch(rectangle)

    if label:

        ax.text(
            x,
            low,
            label,
            ha="center",
            va="top",
            fontsize=10
        )


def create_signal_chart(signal):

    previous = signal["previous"]
    last = signal["last"]

    direction = signal["direction"]

    last_range = max(
        last["high"] - last["low"],
        abs(last["close"] - last["open"]) * 1.5,
        0.00000001
    )

    if direction == "CALL":

        projected_open = last["close"]

        projected_close = (
            projected_open
            + last_range * 0.65
        )

        projected_high = (
            projected_close
            + last_range * 0.20
        )

        projected_low = (
            projected_open
            - last_range * 0.20
        )

    else:

        projected_open = last["close"]

        projected_close = (
            projected_open
            - last_range * 0.65
        )

        projected_high = (
            projected_open
            + last_range * 0.20
        )

        projected_low = (
            projected_close
            - last_range * 0.20
        )

    projected = {
        "open": projected_open,
        "close": projected_close,
        "high": projected_high,
        "low": projected_low
    }

    fig, ax = plt.subplots(
        figsize=(7, 6)
    )

    draw_candle(
        ax,
        1,
        previous,
        "Previous"
    )

    draw_candle(
        ax,
        2,
        last,
        "Last"
    )

    draw_candle(
        ax,
        3,
        projected,
        "Next Trade"
    )

    all_lows = [
        previous["low"],
        last["low"],
        projected["low"]
    ]

    all_highs = [
        previous["high"],
        last["high"],
        projected["high"]
    ]

    minimum = min(all_lows)
    maximum = max(all_highs)

    padding = (
        maximum - minimum
    ) * 0.15

    if padding == 0:
        padding = 1

    ax.set_xlim(
        0.3,
        3.7
    )

    ax.set_ylim(
        minimum - padding,
        maximum + padding
    )

    ax.set_xticks([])

    ax.set_ylabel("Price")

    ax.grid(
        True,
        alpha=0.25
    )

    ax.set_title(
        "Last 2 Candles + Next Trade Candle"
    )

    plt.tight_layout()

    image = BytesIO()

    plt.savefig(
        image,
        format="png",
        dpi=150
    )

    plt.close(fig)

    image.seek(0)

    return image.getvalue()


# =========================================================
# SIGNAL CAPTION
# =========================================================

def format_symbol(symbol):

    if symbol.endswith("USDT"):

        return (
            symbol[:-4]
            + "/USDT"
        )

    return symbol


def make_caption(signal):

    direction_icon = (
        "🟢 CALL"
        if signal["direction"] == "CALL"
        else "🔴 PUT"
    )

    reasons = "\n".join(
        f"• {reason}"
        for reason in signal["reasons"]
    )

    caption = (
        "🏆 BEST SIGNAL\n\n"
        f"📊 Pair: {format_symbol(signal['symbol'])}\n"
        f"🌐 Market: {signal['market']}\n"
        f"🎯 Direction: {direction_icon}\n"
        f"📈 Confidence: {signal['confidence']}%\n"
        f"⏱ Timeframe: 1 Minute\n"
        f"🕯 Entry: Next Candle\n\n"
        f"🔎 Analysis:\n{reasons}\n\n"
        "⚠️ Educational analysis only. "
        "No trade outcome is guaranteed."
    )

    return caption


# =========================================================
# SEND BEST SIGNAL
# =========================================================

def send_best_signal(chat_id):

    send_message(
        chat_id,
        "🔎 Scanning all available live pairs for the best signal..."
    )

    signal = find_best_signal()

    if signal is None:

        send_message(
            chat_id,
            "⚠️ Market data is temporarily unavailable. Please try again."
        )

        return

    chart = create_signal_chart(signal)

    caption = make_caption(signal)

    send_photo(
        chat_id,
        chart,
        caption
    )


# =========================================================
# TELEGRAM COMMAND HANDLER
# =========================================================

def handle_command(chat_id, text):

    global subscribers

    command = (
        text.strip()
        .split()[0]
        .lower()
    )

    if command == "/start":

        with state_lock:

            subscribers.add(chat_id)

        send_message(
            chat_id,
            "🟢 Bot Status: ONLINE\n\n"
            "🏆 Mode: ONE BEST SIGNAL\n"
            f"📊 Live Pairs: {len(PAIRS)}\n\n"
            "Commands:\n"
            "/signal - Find the best signal\n"
            "/status - Check bot status"
        )

    elif command == "/status":

        with state_lock:

            total = len(subscribers)

        send_message(
            chat_id,
            "🟢 Bot Status: ONLINE\n"
            f"📊 Live Pairs: {len(PAIRS)}\n"
            f"👥 Subscribers: {total}\n"
            "🏆 Mode: ONE BEST SIGNAL"
        )

    elif command == "/signal":

        send_best_signal(chat_id)

    else:

        send_message(
            chat_id,
            "❓ Unknown command.\n\n"
            "Use:\n"
            "/signal\n"
            "/status"
        )


# =========================================================
# TELEGRAM LISTENER
# =========================================================

def telegram_listener():

    global last_update_id

    logger.info(
        "Telegram listener started"
    )

    while True:

        try:

            params = {
                "timeout": 30
            }

            if last_update_id is not None:

                params["offset"] = (
                    last_update_id + 1
                )

            response = requests.get(
                f"{TELEGRAM_API}/getUpdates",
                params=params,
                timeout=40
            )

            data = response.json()

            if not data.get("ok"):
                time.sleep(3)
                continue

            updates = data.get(
                "result",
                []
            )

            for update in updates:

                last_update_id = update["update_id"]

                message = update.get("message")

                if not message:
                    continue

                chat = message.get("chat", {})

                chat_id = chat.get("id")

                text = message.get(
                    "text",
                    ""
                )

                if chat_id and text.startswith("/"):

                    logger.info(
                        "Command received: %s",
                        text
                    )

                    handle_command(
                        chat_id,
                        text
                    )

        except Exception as error:

            logger.exception(
                "Telegram listener error: %s",
                error
            )

            time.sleep(5)


# =========================================================
# AUTO SIGNAL ENGINE
# =========================================================

def signal_engine():

    global last_signal_time

    logger.info(
        "Auto signal engine started"
    )

    while True:

        try:

            now = time.time()

            with state_lock:

                active_subscribers = list(
                    subscribers
                )

            if (
                active_subscribers
                and now - last_signal_time
                >= SIGNAL_COOLDOWN
            ):

                signal = find_best_signal()

                if (
                    signal is not None
                    and signal["confidence"]
                    >= MIN_CONFIDENCE
                ):

                    chart = create_signal_chart(signal)

                    caption = make_caption(signal)

                    for chat_id in active_subscribers:

                        try:

                            send_photo(
                                chat_id,
                                chart,
                                caption
                            )

                            time.sleep(1)

                        except Exception as error:

                            logger.warning(
                                "Auto send error: %s",
                                error
                            )

                    last_signal_time = now

            time.sleep(AUTO_SCAN_INTERVAL)

        except Exception as error:

            logger.exception(
                "Signal engine error: %s",
                error
            )

            time.sleep(10)


# =========================================================
# START BOT
# =========================================================

def start_bot():

    if not BOT_TOKEN:

        logger.error(
            "BOT_TOKEN environment variable is missing!"
        )

        return

    try:

        response = requests.get(
            TELEGRAM_TOKEN = ""
