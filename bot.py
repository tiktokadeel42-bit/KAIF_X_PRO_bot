import os
import time
import threading
from datetime import datetime

import requests
from flask import Flask, jsonify

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


# ============================================================
# CONFIGURATION
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "10000"))

# Forex pairs to scan
PAIRS = {
    "EURUSD=X": "EUR/USD",
    "GBPUSD=X": "GBP/USD",
    "USDJPY=X": "USD/JPY",
    "AUDUSD=X": "AUD/USD",
    "NZDUSD=X": "NZD/USD",
    "USDCAD=X": "USD/CAD",
    "USDCHF=X": "USD/CHF",
    "EURJPY=X": "EUR/JPY",
    "GBPJPY=X": "GBP/JPY",
    "EURGBP=X": "EUR/GBP",
    "AUDJPY=X": "AUD/JPY",
    "CADJPY=X": "CAD/JPY"
}

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}"

app = Flask(__name__)

LAST_UPDATE_ID = 0
BOT_STARTED = False


# ============================================================
# RENDER WEB SERVER
# ============================================================

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


# ============================================================
# TELEGRAM FUNCTIONS
# ============================================================

def send_message(chat_id, text):

    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN missing")
        return None

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    try:
        response = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": text
            },
            timeout=30
        )

        return response.json()

    except Exception as error:
        print("Send message error:", error)
        return None


def send_photo(chat_id, photo_path, caption):

    if not TELEGRAM_TOKEN:
        return None

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"

    try:
        with open(photo_path, "rb") as photo:

            response = requests.post(
                url,
                data={
                    "chat_id": chat_id,
                    "caption": caption
                },
                files={
                    "photo": photo
                },
                timeout=60
            )

        return response.json()

    except Exception as error:
        print("Send photo error:", error)
        return None


# ============================================================
# GET LIVE 1 MINUTE MARKET DATA
# ============================================================

def get_market_candles(symbol):

    url = YAHOO_URL.format(symbol)

    params = {
        "range": "1d",
        "interval": "1m"
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

        quote_data = (
            chart
            .get("indicators", {})
            .get("quote", [{}])[0]
        )

        opens = quote_data.get("open", [])
        highs = quote_data.get("high", [])
        lows = quote_data.get("low", [])
        closes = quote_data.get("close", [])

        candles = []

        total = min(
            len(timestamps),
            len(opens),
            len(highs),
            len(lows),
            len(closes)
        )

        for index in range(total):

            o = opens[index]
            h = highs[index]
            l = lows[index]
            c = closes[index]

            if (
                o is None
                or h is None
                or l is None
                or c is None
            ):
                continue

            candles.append({
                "time": timestamps[index],
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c)
            })

        return candles

    except Exception as error:

        print(f"Market data error {symbol}: {error}")

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
            (value - ema) * multiplier
        ) + ema

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

    for i in range(start, len(values)):

        difference = values[i] - values[i - 1]

        if difference > 0:
            gains.append(difference)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(difference))

    average_gain = sum(gains) / period
    average_loss = sum(losses) / period

    if average_loss == 0:
        return 100.0

    rs = average_gain / average_loss

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi


# ============================================================
# ATR / VOLATILITY
# ============================================================

def calculate_atr(candles, period=14):

    if len(candles) < period + 1:
        return 0.0

    true_ranges = []

    start = len(candles) - period

    for i in range(start, len(candles)):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"] - current["low"],
            abs(current["high"] - previous["close"]),
            abs(current["low"] - previous["close"])
        )

        true_ranges.append(tr)

    if not true_ranges:
        return 0.0

    return sum(true_ranges) / len(true_ranges)


# ============================================================
# MACD STYLE MOMENTUM
# ============================================================

def calculate_macd_signal(closes):

    ema_fast = calculate_ema(closes, 12)
    ema_slow = calculate_ema(closes, 26)

    if ema_fast is None or ema_slow is None:
        return 0.0

    return ema_fast - ema_slow


# ============================================================
# CANDLE INFORMATION
# ============================================================

def candle_direction(candle):

    if candle["close"] > candle["open"]:
        return "BULLISH"

    if candle["close"] < candle["open"]:
        return "BEARISH"

    return "NEUTRAL"


def candle_body(candle):

    return abs(
        candle["close"] - candle["open"]
    )


def candle_range(candle):

    return (
        candle["high"] - candle["low"]
    )


def candle_strength(candle):

    full_range = candle_range(candle)

    if full_range == 0:
        return 0.0

    return candle_body(candle) / full_range


# ============================================================
# ANALYSE ONE PAIR
# ============================================================

def analyse_pair(symbol, pair_name):

    candles = get_market_candles(symbol)

    if len(candles) < 35:
        return None

    # IMPORTANT:
    # Last candle can still be running.
    # Therefore we remove it and use only closed candles.

    closed_candles = candles[:-1]

    if len(closed_candles) < 30:
        return None

    closes = [
        candle["close"]
        for candle in closed_candles
    ]

    # Last two COMPLETED candles
    candle_1 = closed_candles[-2]
    candle_2 = closed_candles[-1]

    # Market indicators
    ema_9 = calculate_ema(closes, 9)
    ema_21 = calculate_ema(closes, 21)
    rsi = calculate_rsi(closes, 14)
    atr = calculate_atr(closed_candles, 14)
    macd = calculate_macd_signal(closes)

    if (
        ema_9 is None
        or ema_21 is None
        or atr <= 0
    ):
        return None

    score_call = 0
    score_put = 0

    reasons = []

    direction_1 = candle_direction(candle_1)
    direction_2 = candle_direction(candle_2)

    strength_1 = candle_strength(candle_1)
    strength_2 = candle_strength(candle_2)

    # ========================================================
    # EMA TREND
    # ========================================================

    if ema_9 > ema_21:

        score_call += 20
        reasons.append("EMA bullish trend")

    elif ema_9 < ema_21:

        score_put += 20
        reasons.append("EMA bearish trend")

    # ========================================================
    # PRICE POSITION
    # ========================================================

    last_close = candle_2["close"]

    if last_close > ema_9:

        score_call += 10

    else:

        score_put += 10

    # ========================================================
    # RSI
    # ========================================================

    if 52 <= rsi <= 72:

        score_call += 15

    elif 28 <= rsi <= 48:

        score_put += 15

    # ========================================================
    # MACD MOMENTUM
    # ========================================================

    if macd > 0:

        score_call += 10

    elif macd < 0:

        score_put += 10

    # ========================================================
    # LAST TWO CANDLES PATTERN
    # ========================================================

    if (
        direction_1 == "BULLISH"
        and direction_2 == "BULLISH"
    ):

        score_call += 25
        reasons.append("Last 2 candles bullish")

    elif (
        direction_1 == "BEARISH"
        and direction_2 == "BEARISH"
    ):

        score_put += 25
        reasons.append("Last 2 candles bearish")

    # ========================================================
    # STRONG LAST CANDLE
    # ========================================================

    if strength_2 >= 0.55:

        if direction_2 == "BULLISH":

            score_call += 15
            reasons.append("Strong bullish momentum")

        elif direction_2 == "BEARISH":

            score_put += 15
            reasons.append("Strong bearish momentum")

    # ========================================================
    # DETERMINE NEXT CANDLE DIRECTION
    # ========================================================

    if score_call > score_put:

        direction = "CALL"
        score = score_call

    elif score_put > score_call:

        direction = "PUT"
        score = score_put

    else:

        return None

    # ========================================================
    # CONFIDENCE
    # Not fake 99%.
    # ========================================================

    difference = abs(
        score_call - score_put
    )

    confidence = 50 + min(
        35,
        difference
    )

    # ========================================================
    # BUILD PROJECTED 3RD CANDLE STRUCTURE
    # ========================================================

    projected_open = candle_2["close"]

    average_body = (
        candle_body(candle_1)
        + candle_body(candle_2)
    ) / 2

    projected_body = max(
        average_body,
        atr * 0.35
    )

    projected_body = min(
        projected_body,
        atr * 1.20
    )

    wick_size = max(
        projected_body * 0.25,
        atr * 0.10
    )

    if direction == "CALL":

        projected_close = (
            projected_open + projected_body
        )

        projected_low = (
            projected_open - wick_size
        )

        projected_high = (
            projected_close + wick_size
        )

    else:

        projected_close = (
            projected_open - projected_body
        )

        projected_high = (
            projected_open + wick_size
        )

        projected_low = (
            projected_close - wick_size
        )

    projected_candle = {
        "open": projected_open,
        "high": projected_high,
        "low": projected_low,
        "close": projected_close
    }

    return {
        "symbol": symbol,
        "pair": pair_name,

        "direction": direction,

        "score": score,

        "confidence": confidence,

        "rsi": round(rsi, 2),

        "ema_9": ema_9,

        "ema_21": ema_21,

        "atr": atr,

        "candle_1": candle_1,

        "candle_2": candle_2,

        "projected": projected_candle,

        "reasons": reasons,

        "score_call": score_call,

        "score_put": score_put
    }


# ============================================================
# SCAN ALL PAIRS AND FIND BEST
# ============================================================

def find_best_trade():

    all_signals = []

    for symbol, pair_name in PAIRS.items():

        print(
            f"Scanning {pair_name}..."
        )

        try:

            signal = analyse_pair(
                symbol,
                pair_name
            )

            if signal is not None:

                all_signals.append(signal)

        except Exception as error:

            print(
                f"Analysis error {pair_name}:",
                error
            )

        time.sleep(0.5)

    if not all_signals:
        return None

    # Best pair based on score
    all_signals.sort(
        key=lambda x: (
            x["score"],
            x["confidence"]
        ),
        reverse=True
    )

    return all_signals[0]


# ============================================================
# DRAW ONE CANDLE
# ============================================================

def draw_candle(
    ax,
    x,
    candle,
    color,
    alpha=1.0,
    label=None
):

    open_price = candle["open"]
    close_price = candle["close"]
    high_price = candle["high"]
    low_price = candle["low"]

    # Wick
    ax.plot(
        [x, x],
        [low_price, high_price],
        color=color,
        linewidth=2,
        alpha=alpha
    )

    body_bottom = min(
        open_price,
        close_price
    )

    body_height = abs(
        close_price - open_price
    )

    if body_height == 0:

        body_height = (
            high_price - low_price
        ) * 0.05

    rectangle = Rectangle(
        (
            x - 0.28,
            body_bottom
        ),
        0.56,
        body_height,
        facecolor=color,
        edgecolor=color,
        alpha=alpha
    )

    ax.add_patch(rectangle)

    if label:

        ax.text(
            x,
            high_price,
            label,
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold"
        )


# ============================================================
# CREATE EXACTLY 3 CANDLE IMAGE
# ============================================================

def create_three_candle_chart(signal):

    candle_1 = signal["candle_1"]
    candle_2 = signal["candle_2"]
    candle_3 = signal["projected"]

    fig, ax = plt.subplots(
        figsize=(9, 8)
    )

    # Candle colors
    color_1 = (
        "green"
        if candle_1["close"] >= candle_1["open"]
        else "red"
    )

    color_2 = (
        "green"
        if candle_2["close"] >= candle_2["open"]
        else "red"
    )

    color_3 = (
        "green"
        if signal["direction"] == "CALL"
        else "red"
    )

    # ONLY THREE CANDLES
    draw_candle(
        ax,
        1,
        candle_1,
        color_1,
        label="CANDLE 1"
    )

    draw_candle(
        ax,
        2,
        candle_2,
        color_2,
        label="CANDLE 2"
    )

    # Projected trade candle
    draw_candle(
        ax,
        3,
        candle_3,
        color_3,
        alpha=0.55,
        label="TRADE CANDLE"
    )

    # Mark projected candle OPEN
    ax.annotate(
        "START / OPEN",
        xy=(3, candle_3["open"]),
        xytext=(3.35, candle_3["open"]),
        arrowprops={
            "arrowstyle": "->"
        },
        fontsize=10
    )

    # Mark projected CLOSE
    ax.annotate(
        "EXPECTED END / CLOSE",
        xy=(3, candle_3["close"]),
        xytext=(3.35, candle_3["close"]),
        arrowprops={
            "arrowstyle": "->"
        },
        fontsize=10
    )

    # Mark HIGH
    ax.annotate(
        "HIGH",
        xy=(3, candle_3["high"]),
        xytext=(3.35, candle_3["high"]),
        arrowprops={
            "arrowstyle": "->"
        },
        fontsize=9
    )

    # Mark LOW
    ax.annotate(
        "LOW",
        xy=(3, candle_3["low"]),
        xytext=(3.35, candle_3["low"]),
        arrowprops={
            "arrowstyle": "->"
        },
        fontsize=9
    )

    title = (
        f"{signal['pair']} | "
        f"NEXT 1 MINUTE CANDLE\n"
        f"TRADE: {signal['direction']} | "
        f"Confidence: {signal['confidence']}%"
    )

    ax.set_title(
        title,
        fontsize=14,
        fontweight="bold"
    )

    ax.set_xlim(
        0.3,
        4.8
    )

    ax.set_xticks([
        1,
        2,
        3
    ])

    ax.set_xticklabels([
        "Previous",
        "Latest Closed",
        "Next Trade"
    ])

    ax.set_ylabel("Price")

    ax.grid(
        True,
        alpha=0.25
    )

    # Get all prices for chart padding
    prices = [
        candle_1["low"],
        candle_1["high"],
        candle_2["low"],
        candle_2["high"],
        candle_3["low"],
        candle_3["high"]
    ]

    minimum = min(prices)
    maximum = max(prices)

    padding = (
        maximum - minimum
    ) * 0.15

    if padding == 0:
        padding = maximum * 0.001

    ax.set_ylim(
        minimum - padding,
        maximum + padding
    )

    plt.tight_layout()

    chart_path = "/tmp/three_candle_signal.png"

    plt.savefig(
        chart_path,
        dpi=180,
        bbox_inches="tight"
    )

    plt.close()

    return chart_path


# ============================================================
# CREATE SIGNAL TEXT
# ============================================================

def create_signal_caption(signal):

    projected = signal["projected"]

    reasons = signal["reasons"]

    if reasons:

        analysis_text = "\n".join(
            f"• {reason}"
            for reason in reasons[:5]
        )

    else:

        analysis_text = (
            "• Multi-factor market analysis"
        )

    caption = f"""
🏆 BEST TRADE FOUND

💱 Pair: {signal['pair']}
⏱ Timeframe: 1 MINUTE
🎯 Trade: {signal['direction']}
📊 Confidence: {signal['confidence']}%
📈 RSI: {signal['rsi']}

━━━━━━━━━━━━━━

🕯 LAST 2 CLOSED CANDLES
1️⃣ Previous candle analysed
2️⃣ Latest closed candle analysed

━━━━━━━━━━━━━━

🔮 NEXT TRADE CANDLE
🟢/🔴 Direction: {signal['direction']}

▶️ START / OPEN:
{projected['open']:.5f}

⬆️ EXPECTED HIGH:
{projected['high']:.5f}

⬇️ EXPECTED LOW:
{projected['low']:.5f}

🏁 EXPECTED END / CLOSE:
{projected['close']:.5f}

━━━━━━━━━━━━━━

📌 MARKET ANALYSIS:

{analysis_text}

⚠️ This is a probability-based market projection, not a guaranteed future price.
""".strip()

    return caption


# ============================================================
# PROCESS /SIGNAL
# ============================================================

def process_signal(chat_id):

    send_message(
        chat_id,
        "🔎 Scanning all pairs...\n\n"
        "📊 Analysing market conditions\n"
        "📈 Checking indicators\n"
        "🕯 Checking last 2 CLOSED 1-minute candles\n"
        "🎯 Finding the BEST trade setup..."
