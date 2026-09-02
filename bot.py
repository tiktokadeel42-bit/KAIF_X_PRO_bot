import os
import time
import math
import threading

import requests
from flask import Flask, jsonify

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


# =========================================================
# CONFIG
# =========================================================

BOT_NAME = "KAIF X PRO BOT"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()

PORT = int(os.getenv("PORT", "10000"))

TIMEFRAME = "1 MINUTE"
EXPIRY = "1 MINUTE"

ANALYSIS_INTERVAL = 60
MIN_SCORE = 4


LIVE_PAIRS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CAD": "CAD=X",
    "USD/CHF": "CHF=X",
    "NZD/USD": "NZDUSD=X",
    "EUR/JPY": "EURJPY=X",
    "GBP/JPY": "GBPJPY=X",
    "EUR/GBP": "EURGBP=X",
}


subscribers = set()

lock = threading.Lock()

services_started = False

last_sent_key = None
last_sent_time = 0


CHART_DIR = "generated_charts"

os.makedirs(CHART_DIR, exist_ok=True)


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running", 200


@app.route("/health")
def health():
    return "OK", 200


@app.route("/status")
def status():
    return jsonify({
        "bot": BOT_NAME,
        "telegram_configured": bool(TELEGRAM_TOKEN),
        "subscribers": len(subscribers),
        "live_pairs": len(LIVE_PAIRS),
        "otc": "Requires authorized candle data feed"
    })


# =========================================================
# TELEGRAM
# =========================================================

def tg_url(method):

    return (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/{method}"
    )


def tg_post(method, data=None, files=None, timeout=60):

    if not TELEGRAM_TOKEN:
        return {}

    try:

        response = requests.post(
            tg_url(method),
            data=data or {},
            files=files,
            timeout=timeout
        )

        return response.json()

    except Exception as exc:

        print("Telegram error:", exc)

        return {}


def tg_get_updates(params):

    if not TELEGRAM_TOKEN:
        return {}

    try:

        response = requests.get(
            tg_url("getUpdates"),
            params=params,
            timeout=40
        )

        return response.json()

    except Exception as exc:

        print("Telegram updates error:", exc)

        return {}


def send_message(chat_id, text):

    return tg_post(
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": text,
            "parse_mode": "HTML"
        }
    )


def send_photo(chat_id, path, caption):

    try:

        with open(path, "rb") as image_file:

            return tg_post(
                "sendPhoto",
                {
                    "chat_id": str(chat_id),
                    "caption": caption,
                    "parse_mode": "HTML"
                },
                files={
                    "photo": image_file
                }
            )

    except Exception as exc:

        print("send_photo error:", exc)

        return {}


# =========================================================
# LIVE MARKET DATA
# =========================================================

def get_live_candles(symbol):

    url = (
        "https://query1.finance.yahoo.com/"
        f"v8/finance/chart/{symbol}"
    )

    params = {
        "interval": "1m",
        "range": "1d"
    }

    try:

        response = requests.get(
            url,
            params=params,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
            timeout=20
        )

        response.raise_for_status()

        payload = response.json()

        results = (
            payload
            .get("chart", {})
            .get("result")
            or []
        )

        if not results:
            return []

        result = results[0]

        timestamps = (
            result.get("timestamp")
            or []
        )

        quote_list = (
            result
            .get("indicators", {})
            .get("quote")
            or [{}]
        )

        quote = quote_list[0]

        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []

        candles = []

        count = min(
            len(timestamps),
            len(opens),
            len(highs),
            len(lows),
            len(closes)
        )

        for index in range(count):

            values = (
                opens[index],
                highs[index],
                lows[index],
                closes[index]
            )

            if any(
                value is None
                for value in values
            ):
                continue

            candles.append({
                "timestamp": timestamps[index],
                "open": float(opens[index]),
                "high": float(highs[index]),
                "low": float(lows[index]),
                "close": float(closes[index])
            })

        return candles

    except Exception as exc:

        print(
            f"Market data error for {symbol}:",
            exc
        )

        return []


# =========================================================
# INDICATORS
# =========================================================

def ema(values, period):

    if len(values) < period:
        return None

    value = (
        sum(values[:period])
        / period
    )

    multiplier = (
        2.0
        / (period + 1)
    )

    for price in values[period:]:

        value = (
            (price - value)
            * multiplier
            + value
        )

    return value


def rsi(values, period=14):

    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for index in range(1, len(values)):

        change = (
            values[index]
            - values[index - 1]
        )

        gains.append(
            max(change, 0)
        )

        losses.append(
            max(-change, 0)
        )

    avg_gain = (
        sum(gains[-period:])
        / period
    )

    avg_loss = (
        sum(losses[-period:])
        / period
    )

    if avg_loss == 0:
        return 100.0

    rs = (
        avg_gain
        / avg_loss
    )

    return (
        100.0
        - (
            100.0
            / (1.0 + rs)
        )
    )


def bollinger(values, period=20):

    if len(values) < period:
        return None, None, None

    recent = values[-period:]

    middle = (
        sum(recent)
        / period
    )

    variance = (
        sum(
            (value - middle) ** 2
            for value in recent
        )
        / period
    )

    deviation = math.sqrt(variance)

    upper = (
        middle
        + 2 * deviation
    )

    lower = (
        middle
        - 2 * deviation
    )

    return upper, middle, lower


def atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    ranges = []

    for index in range(
        len(candles) - period,
        len(candles)
    ):

        current = candles[index]

        previous_close = (
            candles[index - 1]["close"]
        )

        true_range = max(
            current["high"]
            - current["low"],

            abs(
                current["high"]
                - previous_close
            ),

            abs(
                current["low"]
                - previous_close
            )
        )

        ranges.append(true_range)

    return (
        sum(ranges)
        / len(ranges)
    )


# =========================================================
# ANALYZE PAIR
# =========================================================

def analyze_pair(pair, symbol):

    candles = get_live_candles(symbol)

    if len(candles) < 50:
        return None

    closes = [
        candle["close"]
        for candle in candles
    ]

    ema9 = ema(closes, 9)

    ema21 = ema(closes, 21)

    rsi_value = rsi(closes, 14)

    upper, middle, lower = (
        bollinger(closes, 20)
    )

    atr_value = atr(candles, 14)

    required_values = (
        ema9,
        ema21,
        rsi_value,
        middle,
        atr_value
    )

    if any(
        value is None
        for value in required_values
    ):
        return None

    last = candles[-1]

    previous = candles[-2]

    price = last["close"]

    call_score = 0
    put_score = 0

    call_reasons = []
    put_reasons = []


    # EMA TREND

    if ema9 > ema21:

        call_score += 1

        call_reasons.append(
            "EMA trend is bullish"
        )

    else:

        put_score += 1

        put_reasons.append(
            "EMA trend is bearish"
        )


    # RSI

    if 50 < rsi_value < 70:

        call_score += 1

        call_reasons.append(
            "RSI is in bullish zone"
        )

    elif 30 < rsi_value < 50:

        put_score += 1

        put_reasons.append(
            "RSI is in bearish zone"
        )


    # BOLLINGER

    if price > middle:

        call_score += 1

        call_reasons.append(
            "Price is above middle trend"
        )

    elif price < middle:

        put_score += 1

        put_reasons.append(
            "Price is below middle trend"
        )


    # LAST CANDLE

    if last["close"] > last["open"]:

        call_score += 1

        call_reasons.append(
            "Last candle is bullish"
        )

    elif last["close"] < last["open"]:

        put_score += 1

        put_reasons.append(
            "Last candle is bearish"
        )


    # LAST TWO CANDLES

    if (
        previous["close"] > previous["open"]
        and last["close"] > last["open"]
    ):

        call_score += 1

        call_reasons.append(
            "Last two candles are bullish"
        )

    elif (
        previous["close"] < previous["open"]
        and last["close"] < last["open"]
    ):

        put_score += 1

        put_reasons.append(
            "Last two candles are bearish"
        )


    # FINAL SIGNAL

    if (
        call_score >= MIN_SCORE
        and call_score > put_score
    ):

        direction = "CALL"

        score = call_score

        reasons = call_reasons

    elif (
        put_score >= MIN_SCORE
        and put_score > call_score
    ):

        direction = "PUT"

        score = put_score

        reasons = put_reasons

    else:

        return None


    confidence = int(
        score * 20
    )


    return {
        "pair": pair,
        "market": "LIVE MARKET",
        "signal": direction,
        "score": score,
        "confidence": confidence,
        "strength": (
            score * 100
            + confidence
        ),
        "price": price,
        "atr": atr_value,
        "reasons": reasons,
        "candles": candles
    }


# =========================================================
# GET BEST SIGNAL
# =========================================================

def get_best_signal():

    candidates = []

    print("Scanning all live pairs...")

    for pair, symbol in LIVE_PAIRS.items():

        try:

            result = analyze_pair(
                pair,
                symbol
            )

            if result:

                candidates.append(result)

                print(
                    f"{pair}: "
                    f"{result['signal']} "
                    f"score={result['score']} "
                    f"confidence={result['confidence']}%"
                )

        except Exception as exc:

            print(
                f"Analysis error for {pair}:",
                exc
            )

        time.sleep(0.2)


    if not candidates:

        return None


    candidates.sort(
        key=lambda item: (
            item["strength"],
            item["score"]
        ),
        reverse=True
    )


    return candidates[0]


# =========================================================
# PROJECT NEXT CANDLE
# =========================================================

def projected_candle(signal):

    price = signal["price"]

    atr_value = signal["atr"]

    body = max(
        atr_value * 0.55,
        price * 0.00003
    )

    wick = max(
        atr_value * 0.25,
        price * 0.00001
    )


    if signal["signal"] == "CALL":

        opening = price

        closing = (
            price + body
        )

        low = (
            opening - wick
        )

        high = (
            closing + wick
        )

    else:

        opening = price

        closing = (
            price - body
        )

        high = (
            opening + wick
        )

        low = (
            closing - wick
        )


    return {
        "open": opening,
        "high": high,
        "low": low,
        "close": closing
    }


# =========================================================
# DRAW CANDLE
# =========================================================

def draw_candle(
    axis,
    x,
    candle,
    alpha=1.0,
    linestyle="-"
):

    bullish = (
        candle["close"]
        >= candle["open"]
    )

    color = (
        "green"
        if bullish
        else "red"
    )


    axis.vlines(
        x,
        candle["low"],
        candle["high"],
        color=color,
        linewidth=2,
        alpha=alpha,
        linestyles=linestyle
    )


    body_low = min(
        candle["open"],
        candle["close"]
    )


    body_height = abs(
        candle["close"]
        - candle["open"]
    )


    minimum = max(
        (
            candle["high"]
            - candle["low"]
        ) * 0.03,
        1e-10
    )


    if body_height < minimum:

        body_height = minimum


    rectangle = Rectangle(
        (
            x - 0.3,
            body_low
        ),
        0.6,
        body_height,
        facecolor=color,
        edgecolor=color,
        alpha=alpha,
        linestyle=linestyle
    )


    axis.add_patch(rectangle)


# =========================================================
# CREATE CHART
# =========================================================

def create_chart(signal):

    previous = (
        signal["candles"][-2]
    )

    last = (
        signal["candles"][-1]
    )

    next_candle = (
        projected_candle(signal)
    )


    figure, axis = plt.subplots(
        figsize=(9, 6)
    )


    figure.patch.set_facecolor(
        "#111111"
    )

    axis.set_facecolor(
        "#111111"
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
        next_candle,
        alpha=0.5,
        linestyle="--"
    )


    axis.set_title(
        "LAST 2 MARKET CANDLES + NEXT TRADE SETUP",
        color="white",
        fontsize=14,
        fontweight="bold"
    )


    axis.set_xticks([
        0,
        1,
        2
    ])


    axis.set_xticklabels(
        [
            "PREVIOUS",
            "LAST CANDLE",
            "NEXT TRADE"
        ],
        color="white"
    )


    axis.tick_params(
        axis="y",
        colors="white"
    )


    for spine in axis.spines.values():

        spine.set_color("#666666")


    prices = [
        previous["low"],
        previous["high"],
        last["low"],
        last["high"],
        next_candle["low"],
        next_candle["high"]
    ]


    price_range = (
        max(prices)
        - min(prices)
    )


    padding = max(
        price_range * 0.2,
        signal["price"] * 0.00005
    )


    axis.set_ylim(
        min(prices) - padding,
        max(prices) + padding
    )


    axis.set_xlim(
        -0.8,
        2.8
    )


    axis.grid(
        True,
        linestyle=":",
        alpha=0.25
    )


    filename = os.path.join(
        CHART_DIR,
        f"signal_{int(time.time())}.png"
    )


    plt.tight_layout()


    plt.savefig(
        filename,
        dpi=150,
        bbox_inches="tight"
    )


    plt.close(figure)


    return filename, next_candle


# =========================================================
# SIGNAL CAPTION
# =========================================================

def make_caption(
    signal,
    next_candle
):

    if signal["signal"] == "CALL":

        direction = "🟢 CALL / UP"

    else:

        direction = "🔴 PUT / DOWN"


    reasons = "\n".join(
        f"• {reason}"
        for reason in signal["reasons"]
    )


    return (
        f"👑 <b>{BOT_NAME}</b>\n\n"

        f"🏆 <b>BEST SINGLE SIGNAL</b>\n\n"

        f"🌐 <b>MARKET:</b> {signal['market']}\n"

        f"💱 <b>PAIR:</b> {signal['pair']}\n"

        f"{direction}\n"

        f"📊 <b>CONFIDENCE:</b> "
        f"{signal['confidence']}%\n"

        f"⭐ <b>SCORE:</b> "
        f"{signal['score']}/5\n"

        f"⏱ <b>TIMEFRAME:</b> "
        f"{TIMEFRAME}\n"

        f"⌛ <b>EXPIRY:</b> "
        f"{EXPIRY}\n\n"

        f"━━━━━━━━━━━━━━━━\n\n"

        f"🎯 <b>TRADE:</b> NEXT CANDLE\n"

        f"💰 <b>ENTRY:</b> "
        f"{next_candle['open']:.5f}\n"

        f"📈 <b>PROJECTED HIGH:</b> "
        f"{next_candle['high']:.5f}\n"

        f"📉 <b>PROJECTED LOW:</b> "
        f"{next_candle['low']:.5f}\n\n"

        f"━━━━━━━━━━━━━━━━\n\n"

        f"🔎 <b>ANALYSIS:</b>\n\n"

        f"{reasons}\n\n"

        f"⚠️ <i>Technical analysis only. "
        f"No profit is guaranteed.</i>"
    )


# =========================================================
# SEND ONE SIGNAL
# =========================================================

def send_best_signal(chat_id):

    best = get_best_signal()


    if not best:

        send_message(
            chat_id,
            "⚠️ No strong signal found right now."
        )

        return


    path = None


    try:

        path, next_candle = create_chart(best)

        caption = make_caption(
            best,
            next_candle
        )


        result = send_photo(
            chat_id,
            path,
            caption
        )


        if not result.get("ok"):

            send_message(
                chat_id,
                caption
            )


    finally:

        if (
            path
            and os.path.exists(path)
        ):

            os.remove(path)


# =========================================================
# AUTOMATIC SIGNAL ENGINE
# =========================================================

def signal_engine():

    global last_sent_key
    global last_sent_time


    while True:

        try:

            if not subscribers:

                time.sleep(10)

                continue


            best = get_best_signal()


            if not best:

                print(
                    "No strong signal found."
                )

                time.sleep(
                    ANALYSIS_INTERVAL
                )

                continue


            key = (
                best["pair"],
                best["signal"],
                best["score"],
                round(
                    best["price"],
                    5
                )
            )


            now = time.time()


            if (
                key == last_sent_key
                and (
                    now - last_sent_time
                    < ANALYSIS_INTERVAL
                )
            ):

                time.sleep(
                    ANALYSIS_INTERVAL
                )

                continue


            path = None


            try:

                path, next_candle = create_chart(best)

                caption = make_caption(
                    best,
                    next_candle
                )


                for chat_id in list(subscribers):

                    send_photo(
                        chat_id,
                        path,
    
