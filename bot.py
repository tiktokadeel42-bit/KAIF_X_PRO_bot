import os
import time
import threading
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "10000"))

PAIRS = {
    "EURUSD=X": "EUR/USD",
    "GBPUSD=X": "GBP/USD",
    "USDJPY=X": "USD/JPY",
    "AUDUSD=X": "AUD/USD",
    "USDCAD=X": "USD/CAD",
    "USDCHF=X": "USD/CHF",
    "EURJPY=X": "EUR/JPY",
    "GBPJPY=X": "GBP/JPY",
}

app = Flask(__name__)
last_update_id = None


@app.get("/")
def home():
    return jsonify({
        "status": "online",
        "time": datetime.now(timezone.utc).isoformat()
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


def tg(method, data=None, files=None):
    if not TOKEN:
        print("TELEGRAM_TOKEN is missing")
        return None

    url = f"https://api.telegram.org/bot{TOKEN}/{method}"

    try:
        if files is not None:
            response = requests.post(
                url,
                data=data,
                files=files,
                timeout=60
            )
        else:
            response = requests.post(
                url,
                data=data,
                timeout=30
            )

        return response.json()

    except Exception as error:
        print("Telegram error:", error)
        return None


def send_message(chat_id, text):
    return tg(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text
        }
    )


def send_photo(chat_id, path, caption):
    try:
        with open(path, "rb") as photo:
            return tg(
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
        print("Photo error:", error)
        return None


def get_candles(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

    try:
        response = requests.get(
            url,
            params={
                "range": "1d",
                "interval": "1m"
            },
            headers={
                "User-Agent": "Mozilla/5.0"
            },
            timeout=20
        )

        response.raise_for_status()

        result = response.json()["chart"]["result"][0]
        quote = result["indicators"]["quote"][0]

        output = []

        for o, h, l, c in zip(
            quote["open"],
            quote["high"],
            quote["low"],
            quote["close"]
        ):
            if None not in (o, h, l, c):
                output.append({
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c)
                })

        return output

    except Exception as error:
        print("Data error:", symbol, error)
        return []


def calculate_ema(values, period):
    if len(values) < period:
        return None

    value = sum(values[:period]) / period
    multiplier = 2 / (period + 1)

    for price in values[period:]:
        value = (
            price * multiplier
            + value * (1 - multiplier)
        )

    return value


def calculate_rsi(values, period=14):
    if len(values) <= period:
        return 50.0

    changes = []

    start = len(values) - period

    for index in range(start, len(values)):
        changes.append(
            values[index] - values[index - 1]
        )

    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]

    average_gain = sum(gains) / period
    average_loss = sum(losses) / period

    if average_loss == 0:
        return 100.0

    return 100 - (
        100 / (
            1 + average_gain / average_loss
        )
    )


def calculate_atr(candles, period=14):
    if len(candles) <= period:
        return 0.0

    ranges = []

    start = len(candles) - period

    for index in range(start, len(candles)):
        previous_close = candles[index - 1]["close"]
        current = candles[index]

        ranges.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - previous_close),
                abs(current["low"] - previous_close)
            )
        )

    return sum(ranges) / len(ranges)


def candle_body(candle):
    return abs(
        candle["close"] - candle["open"]
    )


def analyse_pair(symbol, name):
    raw_candles = get_candles(symbol)

    if len(raw_candles) < 35:
        return None

    # Running candle remove.
    # Only completed candles are used.
    closed = raw_candles[:-1]

    candle_1 = closed[-2]
    candle_2 = closed[-1]

    closes = [
        candle["close"]
        for candle in closed
    ]

    ema_fast = calculate_ema(closes, 9)
    ema_slow = calculate_ema(closes, 21)
    market_rsi = calculate_rsi(closes)
    market_atr = calculate_atr(closed)

    if (
        ema_fast is None
        or ema_slow is None
        or market_atr <= 0
    ):
        return None

    bullish_score = 0
    bearish_score = 0

    # Trend
    if ema_fast > ema_slow:
        bullish_score += 25
    else:
        bearish_score += 25

    # RSI
    if market_rsi >= 55:
        bullish_score += 15
    elif market_rsi <= 45:
        bearish_score += 15

    # Latest candle
    if candle_2["close"] > candle_2["open"]:
        bullish_score += 20
    else:
        bearish_score += 20

    # Last two candles
    if (
        candle_1["close"] > candle_1["open"]
        and candle_2["close"] > candle_2["open"]
    ):
        bullish_score += 20

    if (
        candle_1["close"] < candle_1["open"]
        and candle_2["close"] < candle_2["open"]
    ):
        bearish_score += 20

    # Price position
    if candle_2["close"] > ema_fast:
        bullish_score += 10
    else:
        bearish_score += 10

    if bullish_score >= bearish_score:
        direction = "CALL"
        score = bullish_score
    else:
        direction = "PUT"
        score = bearish_score

    confidence = min(
        85,
        50 + abs(bullish_score - bearish_score)
    )

    # NEXT / THIRD CANDLE PROJECTION
    average_body = max(
        (
            candle_body(candle_1)
            + candle_body(candle_2)
        ) / 2,
        market_atr * 0.25
    )

    wick = max(
        average_body * 0.25,
        market_atr * 0.08
    )

    projected_open = candle_2["close"]

    if direction == "CALL":
        projected_close = (
            projected_open + average_body
        )

        projected = {
            "open": projected_open,
            "close": projected_close,
            "low": projected_open - wick,
            "high": projected_close + wick
        }

    else:
        projected_close = (
            projected_open - average_body
        )

        projected = {
            "open": projected_open,
            "close": projected_close,
            "low": projected_close - wick,
            "high": projected_open + wick
        }

    return {
        "pair": name,
        "direction": direction,
        "score": score,
        "confidence": confidence,
        "rsi": market_rsi,
        "candle_1": candle_1,
        "candle_2": candle_2,
        "candle_3": projected
    }


def find_best_signal():
    signals = []

    for symbol, name in PAIRS.items():
        print("Scanning:", name)

        signal = analyse_pair(symbol, name)

        if signal is not None:
            signals.append(signal)

        time.sleep(0.3)

    if not signals:
        return None

    return max(
        signals,
        key=lambda item: (
            item["score"],
            item["confidence"]
        )
    )


def draw_candle(ax, x, candle, predicted=False):
    bullish = (
        candle["close"] >= candle["open"]
    )

    color = "green" if bullish else "red"

    alpha = 0.45 if predicted else 1.0

    ax.plot(
        [x, x],
        [candle["low"], candle["high"]],
        color=color,
        linewidth=2,
        alpha=alpha
    )

    bottom = min(
        candle["open"],
        candle["close"]
    )

    height = max(
        abs(
            candle["close"]
            - candle["open"]
        ),
        (
            candle["high"]
            - candle["low"]
        ) * 0.02
    )

    rectangle = Rectangle(
        (x - 0.25, bottom),
        0.5,
        height,
        facecolor=color,
        edgecolor=color,
        alpha=alpha
    )

    ax.add_patch(rectangle)


def create_chart(signal):
    fig, ax = plt.subplots(
        figsize=(7, 8)
    )

    candle_1 = signal["candle_1"]
    candle_2 = signal["candle_2"]
    candle_3 = signal["candle_3"]

    # EXACTLY THREE CANDLES
    draw_candle(
        ax,
        1,
        candle_1
    )

    draw_candle(
        ax,
        2,
        candle_2
    )

    draw_candle(
        ax,
        3,
        candle_3,
        predicted=True
    )

    # Third candle structure labels
    ax.annotate(
        "OPEN / START",
        xy=(3, candle_3["open"]),
        xytext=(3.4, candle_3["open"]),
        arrowprops={"arrowstyle": "->"}
    )

    ax.annotate(
        "EXPECTED CLOSE",
        xy=(3, candle_3["close"]),
        xytext=(3.4, candle_3["close"]),
        arrowprops={"arrowstyle": "->"}
    )

    ax.annotate(
        "HIGH",
        xy=(3, candle_3["high"]),
        xytext=(3.4, candle_3["high"]),
        arrowprops={"arrowstyle": "->"}
    )

    ax.annotate(
        "LOW",
        xy=(3, candle_3["low"]),
        xytext=(3.4, candle_3["low"]),
        arrowprops={"arrowstyle": "->"}
    )

    ax.set_title(
        f"{signal['pair']} | NEXT 1 MINUTE CANDLE\n"
        f"TRADE: {signal['direction']}"
    )

    ax.set_xticks([1, 2, 3])

    ax.set_xticklabels([
        "Closed Candle 1",
        "Closed Candle 2",
        "Projected Trade Candle"
    ])

    ax.set_xlim(0.4, 5.0)

    prices = []

    for candle in [
        candle_1,
        candle_2,
        candle_3
    ]:
        prices.append(candle["low"])
        prices.append(candle["high"])

    minimum = min(prices)
    maximum = max(prices)

    padding = max(
        maximum - minimum,
        0.000001
    ) * 0.15

    ax.set_ylim(
        minimum - padding,
        maximum + padding
    )

    ax.grid(alpha=0.25)

    path = "/tmp/signal.png"

    plt.tight_layout()

    plt.savefig(
        path,
        dpi=180
    )

    plt.close(fig)

    return path


def process_signal(chat_id):
    send_message(
        chat_id,
        "Scanning 1-minute market data and finding the best setup..."
    )

    signal = find_best_signal()

    if signal is None:
        send_message(
            chat_id,
            "Market data is temporarily unavailable. Try again in a moment."
        )
        return

    path = create_chart(signal)

    candle = signal["candle_3"]

    caption = (
        f"BEST SETUP\n\n"
        f"Pair: {signal['pair']}\n"
        f"Timeframe: 1 minute\n"
        f"Direction: {signal['direction']}\n"
        f"Confidence score: {signal['confidence']}%\n"
        f"RSI: {signal['rsi']:.1f}\n\n"
        f"NEXT CANDLE PROJECTION\n"
        f"Open: {candle['open']:.5f}\n"
        f"High: {candle['high']:.5f}\n"
        f"Low: {candle['low']:.5f}\n"
        f"Expected close: {candle['close']:.5f}\n\n"
        f"Probability-based projection, not a guarantee."
    )

    send_photo(
        chat_id,
        path,
        caption
    )

    if os.path.exists(path):
        os.remove(path)


def bot_loop():
    global last_update_id

    while True:
        try:
            if not TOKEN:
                print("Waiting for TELEGRAM_TOKEN...")
                time.sleep(10)
                continue

            params = {
                "timeout": 30
            }

            if last_update_id is not None:
                params["offset"] = (
                    last_update_id + 1
                )

            response = requests.get(
                f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                params=params,
                timeout=40
            )

            data = response.json()

            for update in data.get(
                "result",
                []
            ):
                last_update_id = update["update_id"]

                message = update.get("message")

                if not message:
                    continue

                chat_id = message["chat"]["id"]

                text = message.get(
                    "text",
                    ""
                ).strip().lower()

                if text == "/start":
                    send_message(
                        chat_id,
                        "Bot online. Use /signal"
                    )

                elif text == "/signal":
                    process_signal(chat_id)

                else:
                    send_message(
                        chat_id,
                        "Use /signal"
                    )

        except Exception as error:
            print("Bot loop error:", error)
            time.sleep(5)


if __name__ == "__main__":
    threading.Thread(
        target=bot_loop,
        daemon=True
    ).start()

    app.run(
        host="0.0.0.0",
        port=PORT
    )
