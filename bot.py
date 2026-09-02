import os
import time
import math
import threading
from datetime import datetime

import requests

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from flask import Flask, jsonify


# =========================================================
# CONFIG
# =========================================================

BOT_NAME = "KAIF X PRO BOT"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()

TIMEFRAME = "1 MINUTE"
EXPIRY = "1 MINUTE"

ANALYSIS_INTERVAL = 60
MIN_SCORE = 4

CHART_DIR = "generated_charts"
os.makedirs(CHART_DIR, exist_ok=True)


# =========================================================
# LIVE MARKET PAIRS
# =========================================================

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


# =========================================================
# OTC PAIRS
#
# IMPORTANT:
# These are only pair names for the OTC module.
# Real candles must come from an authorized/valid data source.
# =========================================================

OTC_PAIRS = [
    "EUR/USD OTC",
    "GBP/USD OTC",
    "USD/JPY OTC",
    "AUD/USD OTC",
    "USD/CAD OTC",
    "USD/CHF OTC",
    "EUR/JPY OTC",
    "GBP/JPY OTC",
]


subscribers = set()

state_lock = threading.Lock()

services_started = False

last_sent_key = None
last_sent_time = 0


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return f"{BOT_NAME} is running"


@app.route("/health")
def health():
    return "OK", 200


@app.route("/status")
def status():
    return jsonify({
        "bot": BOT_NAME,
        "telegram": bool(TELEGRAM_TOKEN),
        "subscribers": len(subscribers),
        "live_pairs": len(LIVE_PAIRS),
        "otc_pairs": len(OTC_PAIRS),
        "mode": "BEST SINGLE SIGNAL"
    })


# =========================================================
# TELEGRAM
# =========================================================

TELEGRAM_BASE = "https://api.telegram.org"


def telegram_url(method):
    return f"{TELEGRAM_BASE}/bot{TELEGRAM_TOKEN}/{method}"


def telegram_request(method, data=None):

    if not TELEGRAM_TOKEN:
        return {}

    try:

        response = requests.post(
            telegram_url(method),
            data=data or {},
            timeout=30
        )

        return response.json()

    except Exception as e:

        print("Telegram error:", e)

        return {}


def telegram_get_updates(params):

    if not TELEGRAM_TOKEN:
        return {}

    try:

        response = requests.get(
            telegram_url("getUpdates"),
            params=params,
            timeout=40
        )

        return response.json()

    except Exception as e:

        print("Telegram updates error:", e)

        return {}


def send_message(chat_id, text):

    return telegram_request(
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": text,
            "parse_mode": "HTML"
        }
    )


def send_photo(chat_id, path, caption):

    if not TELEGRAM_TOKEN:
        return {}

    try:

        with open(path, "rb") as photo:

            response = requests.post(
                telegram_url("sendPhoto"),
                data={
                    "chat_id": str(chat_id),
                    "caption": caption,
                    "parse_mode": "HTML"
                },
                files={
                    "photo": photo
                },
                timeout=60
            )

        return response.json()

    except Exception as e:

        print("Photo error:", e)

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

        if response.status_code != 200:
            return []

        data = response.json()

        result = (
            data.get("chart", {})
            .get("result", [])
        )

        if not result:
            return []

        result = result[0]

        timestamps = result.get(
            "timestamp",
            []
        )

        quote = (
            result.get("indicators", {})
            .get("quote", [{}])[0]
        )

        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])

        candles = []

        for i in range(
            min(
                len(timestamps),
                len(opens),
                len(highs),
                len(lows),
                len(closes)
            )
        ):

            values = [
                opens[i],
                highs[i],
                lows[i],
                closes[i]
            ]

            if any(v is None for v in values):
                continue

            candles.append({
                "timestamp": timestamps[i],
                "open": float(opens[i]),
                "high": float(highs[i]),
                "low": float(lows[i]),
                "close": float(closes[i])
            })

        return candles

    except Exception as e:

        print(
            "Live market error:",
            symbol,
            e
        )

        return []


# =========================================================
# OTC MARKET DATA
#
# CONNECT YOUR AUTHORIZED DATA SOURCE HERE
# =========================================================

def get_otc_candles(pair):

    """
    This function intentionally returns an empty list
    until a valid authorized OTC candle feed is connected.

    Expected output format:

    [
        {
            "timestamp": 1234567890,
            "open": 1.10000,
            "high": 1.10100,
            "low": 1.09900,
            "close": 1.10050
        }
    ]
    """

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

        ema = (
            (value - ema)
            * multiplier
            + ema
        )

    return ema


def calculate_rsi(values, period=14):

    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):

        change = values[i] - values[i - 1]

        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    recent_gains = gains[-period:]
    recent_losses = losses[-period:]

    avg_gain = (
        sum(recent_gains)
        / period
    )

    avg_loss = (
        sum(recent_losses)
        / period
    )

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def calculate_bollinger(values, period=20):

    if len(values) < period:
        return None, None, None

    recent = values[-period:]

    middle = sum(recent) / period

    variance = sum(
        (x - middle) ** 2
        for x in recent
    ) / period

    std = math.sqrt(variance)

    upper = middle + 2 * std
    lower = middle - 2 * std

    return upper, middle, lower


def calculate_atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    ranges = []

    for i in range(
        len(candles) - period,
        len(candles)
    ):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"] - current["low"],
            abs(
                current["high"]
                - previous["close"]
            ),
            abs(
                current["low"]
                - previous["close"]
            )
        )

        ranges.append(tr)

    return sum(ranges) / len(ranges)


# =========================================================
# ANALYZE CANDLES
# =========================================================

def analyze_candles(
    pair,
    market_type,
    candles
):

    if len(candles) < 50:
        return None

    closes = [
        c["close"]
        for c in candles
    ]

    price = closes[-1]

    ema9 = calculate_ema(closes, 9)
    ema21 = calculate_ema(closes, 21)

    rsi = calculate_rsi(closes, 14)

    upper, middle, lower = (
        calculate_bollinger(
            closes,
            20
        )
    )

    atr = calculate_atr(
        candles,
        14
    )

    if any(
        x is None
        for x in [
            ema9,
            ema21,
            rsi,
            middle,
            atr
        ]
    ):
        return None

    call_score = 0
    put_score = 0

    reasons_call = []
    reasons_put = []

    # EMA

    if ema9 > ema21:

        call_score += 1
        reasons_call.append(
            "EMA trend bullish"
        )

    else:

        put_score += 1
        reasons_put.append(
            "EMA trend bearish"
        )

    # RSI

    if 50 < rsi < 70:

        call_score += 1
        reasons_call.append(
            "RSI bullish zone"
        )

    elif 30 < rsi < 50:

        put_score += 1
        reasons_put.append(
            "RSI bearish zone"
        )

    # Bollinger

    if price > middle:

        call_score += 1
        reasons_call.append(
            "Price above middle trend"
        )

    else:

        put_score += 1
        reasons_put.append(
            "Price below middle trend"
        )

    # Last candle

    last = candles[-1]

    if last["close"] > last["open"]:

        call_score += 1
        reasons_call.append(
            "Last candle bullish"
        )

    elif last["close"] < last["open"]:

        put_score += 1
        reasons_put.append(
            "Last candle bearish"
        )

    # Previous candle

    previous = candles[-2]

    if (
        previous["close"]
        > previous["open"]
        and last["close"]
        > last["open"]
    ):

        call_score += 1
        reasons_call.append(
            "Two candles bullish"
        )

    elif (
        previous["close"]
        < previous["open"]
        and last["close"]
        < last["open"]
    ):

        put_score += 1
        reasons_put.append(
            "Two candles bearish"
        )

    # Final

    if (
        call_score >= MIN_SCORE
        and call_score > put_score
    ):

        direction = "CALL"
        score = call_score
        reasons = reasons_call

    elif (
        put_score >= MIN_SCORE
        and put_score > call_score
    ):

        direction = "PUT"
        score = put_score
        reasons = reasons_put

    else:

        return None

    confidence = int(
        (score / 5) * 100
    )

    strength = (
        score * 100
        + confidence
    )

    return {
        "pair": pair,
        "market": market_type,
        "signal": direction,
        "score": score,
        "confidence": confidence,
        "strength": strength,
        "price": price,
        "rsi": rsi,
        "atr": atr,
        "reasons": reasons,
        "candles": candles
    }


# =========================================================
# SCAN LIVE MARKET
# =========================================================

def scan_live_market():

    results = []

    for pair, symbol in LIVE_PAIRS.items():

        try:

            candles = get_live_candles(symbol)

            result = analyze_candles(
                pair,
                "LIVE MARKET",
                candles
            )

            if result:
                results.append(result)

        except Exception as e:

            print(
                "Live scan error:",
                pair,
                e
            )

        time.sleep(0.3)

    return results


# =========================================================
# SCAN OTC MARKET
# =========================================================

def scan_otc_market():

    results = []

    for pair in OTC_PAIRS:

        try:

            candles = get_otc_candles(pair)

            if not candles:
                continue

            result = analyze_candles(
                pair,
                "OTC MARKET",
                candles
            )

            if result:
                results.append(result)

        except Exception as e:

            print(
                "OTC scan error:",
                pair,
                e
            )

    return results


# =========================================================
# GET BEST SIGNAL FROM BOTH MARKETS
# =========================================================

def get_best_signal():

    print("\nScanning LIVE MARKET...")

    live_results = scan_live_market()

    print("Scanning OTC MARKET...")

    otc_results = scan_otc_market()

    all_results = (
        live_results
        + otc_results
    )

    if not all_results:

        return None

    all_results.sort(
        key=lambda x: (
            x["strength"],
            x["score"],
            x["confidence"]
        ),
        reverse=True
    )

    return all_results[0]


# =========================================================
# PROJECT NEXT CANDLE
# =========================================================

def build_projected_candle(signal):

    price = signal["price"]
    atr = signal["atr"]

    body = max(
        atr * 0.55,
        price * 0.00003
    )

    wick = max(
        atr * 0.25,
        price * 0.00001
    )

    if signal["signal"] == "CALL":

        opening = price
        closing = price + body

        low = opening - wick
        high = closing + wick

    else:

        opening = price
        closing = price - body

        high = opening + wick
        low = closing - wick

    return {
        "open": opening,
        "close": closing,
        "high": high,
        "low": low
    }


# =========================================================
# DRAW CANDLE
# =========================================================

def draw_candle(
    ax,
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
        "#00c853"
        if bullish
        else "#ff1744"
    )

    ax.vlines(
        x,
        candle["low"],
        candle["high"],
        color=color,
        linewidth=2,
        alpha=alpha,
        linestyles=linestyle
    )

    low_body = min(
        candle["open"],
        candle["close"]
    )

    height = abs(
        candle["close"]
        - candle["open"]
    )

    minimum = (
        candle["high"]
        - candle["low"]
    ) * 0.04

    if height < minimum:
        height = minimum

    rectangle = Rectangle(
        (
            x - 0.3,
            low_body
        ),
        0.6,
        height,
        facecolor=color,
        edgecolor=color,
        alpha=alpha,
        linestyle=linestyle
    )

    ax.add_patch(rectangle)


# =========================================================
# CREATE SIGNAL IMAGE
# =========================================================

def create_signal_chart(signal):

    candles = signal["candles"]

    previous = candles[-2]
    last = candles[-1]

    projected = build_projected_candle(
        signal
    )

    fig, ax = plt.subplots(
        figsize=(9, 6)
    )

    fig.patch.set_facecolor("#111111")
    ax.set_facecolor("#111111")

    draw_candle(ax, 0, previous)

    draw_candle(ax, 1, last)

    draw_candle(
        ax,
        2,
        projected,
        alpha=0.5,
        linestyle="--"
    )

    ax.set_xticks([0, 1, 2])

    ax.set_xticklabels(
        [
            "PREVIOUS",
            "LAST CANDLE",
            "NEXT TRADE"
        ],
        color="white"
    )

    ax.tick_params(
        axis="y",
        colors="white"
    )

    for spine in ax.spines.values():
        spine.set_color("#666666")

    ax.grid(
        True,
        linestyle=":",
        alpha=0.25
    )

    ax.set_title(
        "LAST 2 MARKET CANDLES + NEXT TRADE SETUP",
        color="white",
        fontsize=14,
        fontweight="bold"
    )

    prices = [
        previous["low"],
        previous["high"],
        last["low"],
        last["high"],
        projected["low"],
        projected["high"]
    ]

    padding = (
        max(prices)
        - min(prices)
    ) * 0.2

    ax.set_ylim(
        min(prices) - padding,
        max(prices) + padding
    )

    filename = (
        f"{CHART_DIR}/signal_"
        f"{int(time.time())}.png"
    )

    plt.tight_layout()

    plt.savefig(
        filename,
        dpi=150
    )

    plt.close()

    return filename, projected


# =========================================================
# CAPTION
# =========================================================

def make_caption(
    signal,
    projected
):

    emoji = (
        "🟢"
        if signal["signal"] == "CALL"
        else "🔴"
    )

    reasons = "\n".join(
        f"• {x}"
        for x in signal["reasons"]
    )

    return f"""
👑 <b>{BOT_NAME}</b>

🏆 <b>BEST SINGLE SIGNAL</b>

🌐 <b>MARKET:</b> {signal["market"]}

💱 <b>PAIR:</b> {signal["pair"]}

{emoji} <b>DIRECTION:</b> {signal["signal"]}

📊 <b>CONFIDENCE:</b> {signal["confidence"]}%

⭐ <b>SCORE:</b> {signal["score"]}/5

⏱ <b>TIMEFRAME:</b> {TIMEFRAME}

⌛ <b>EXPIRY:</b> {EXPIRY}

━━━━━━━━━━━━━━━━

🎯 <b>TRADE:</b> NEXT CANDLE

💰 <b>ENTRY:</b> {projected["open"]:.5f}

📈 <b>PROJECTED HIGH:</b> {projected["high"]:.5f}

📉 <b>PROJECTED LOW:</b> {projected["low"]:.5f}

━━━━━━━━━━━━━━━━

🔎 <b>ANALYSIS:</b>

{reasons}

━━━━━━━━━━━━━━━━

⚠️ <i>Technical analysis only. No trade or profit is guaranteed.</i>
"""


# =========================================================
# SIGNAL ENGINE
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

                print("No strong signal.")

                time.sleep(
                    ANALYSIS_INTERVAL
                )

                continue

            key = (
                best["market"],
                best["pair"],
                best["signal"],
                round(best["price"], 5)
            )

            now = time.time()

            if (
                key == last_sent_key
                and now - last_sent_time
                < ANALYSIS_INTERVAL
            ):

                time.sleep(
                    ANALYSIS_INTERVAL
                )

                continue

            path, projected = (
                create_signal_chart(best)
            )

            caption = make_caption(
                best,
                projected
            )

            for chat_id in list(subscribers):

                send_photo(
                    chat_id,
                    path,
                    caption
                )

            last_sent_key = key
            last_sent_time = now

            if os.path.exists(path):
                os.remove(path)

            time.sleep(
                ANALYSIS_INTERVAL
            )

        except Exception as e:

            print(
                "Engine error:"
