import os
import time
import threading
import requests
from datetime import datetime
from flask import Flask

# =========================================================
# CONFIG
# =========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

BOT_NAME = "KAIF X PRO"

PAIRS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "USDJPY=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CAD": "USDCAD=X",
    "USD/CHF": "USDCHF=X",
    "NZD/USD": "NZDUSD=X",
    "EUR/JPY": "EURJPY=X",
    "GBP/JPY": "GBPJPY=X",
    "EUR/GBP": "EURGBP=X"
}

TIMEFRAME = "1m"
EXPIRY = "1 Minute"

MIN_SCORE = 4

subscribers = set()
last_signal = {}


# =========================================================
# FLASK SERVER FOR RENDER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return f"{BOT_NAME} Telegram Signal Bot is running."


@app.route("/health")
def health():
    return "OK"


# =========================================================
# TELEGRAM REQUEST
# =========================================================

def telegram_request(method, data=None):

    if not TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_TOKEN is missing.")
        return {}

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/{method}"
    )

    try:

        response = requests.post(
            url,
            json=data or {},
            timeout=30
        )

        result = response.json()

        if not result.get("ok"):
            print(
                f"Telegram API error ({method}):",
                result
            )

        return result

    except Exception as e:

        print(
            f"Telegram request error ({method}):",
            e
        )

        return {}


# =========================================================
# SEND MESSAGE
# =========================================================

def send_message(chat_id, text):

    return telegram_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
    )


# =========================================================
# TELEGRAM CONNECTION TEST
# =========================================================

def test_telegram():

    print("--------------------------------")
    print("Testing Telegram connection...")
    print("--------------------------------")

    result = telegram_request("getMe")

    if result.get("ok"):

        bot = result.get("result", {})

        print(
            "Telegram connected successfully."
        )

        print(
            "Bot username:",
            bot.get("username")
        )

        print(
            "Bot name:",
            bot.get("first_name")
        )

        return True

    print(
        "Telegram connection FAILED."
    )

    print(
        "Please check TELEGRAM_TOKEN."
    )

    return False


# =========================================================
# REMOVE WEBHOOK
# =========================================================

def remove_webhook():

    print("--------------------------------")
    print("Removing Telegram webhook...")
    print("--------------------------------")

    result = telegram_request(
        "deleteWebhook",
        {
            "drop_pending_updates": True
        }
    )

    if result.get("ok"):

        print(
            "Telegram webhook removed successfully."
        )

    else:

        print(
            "Could not remove Telegram webhook."
        )


# =========================================================
# MARKET DATA
# =========================================================

def get_candles(symbol):

    url = (
        "https://query1.finance.yahoo.com/"
        f"v8/finance/chart/{symbol}"
    )

    params = {
        "interval": "1m",
        "range": "1d"
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

        if response.status_code != 200:

            print(
                "Yahoo HTTP error:",
                symbol,
                response.status_code
            )

            return []

        data = response.json()

        chart = data.get(
            "chart",
            {}
        )

        result = chart.get(
            "result"
        )

        if not result:

            print(
                "No market data:",
                symbol
            )

            return []

        quote = result[0][
            "indicators"
        ]["quote"][0]

        opens = quote.get(
            "open",
            []
        )

        highs = quote.get(
            "high",
            []
        )

        lows = quote.get(
            "low",
            []
        )

        closes = quote.get(
            "close",
            []
        )

        candles = []

        length = min(
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

            candles.append(
                {
                    "open": float(opens[i]),
                    "high": float(highs[i]),
                    "low": float(lows[i]),
                    "close": float(closes[i])
                }
            )

        return candles

    except Exception as e:

        print(
            "Market data error:",
            symbol,
            e
        )

        return []


# =========================================================
# EMA
# =========================================================

def calculate_ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    ema_value = (
        sum(values[:period]) / period
    )

    for price in values[period:]:

        ema_value = (
            (price - ema_value)
            * multiplier
            + ema_value
        )

    return ema_value


# =========================================================
# RSI
# =========================================================

def calculate_rsi(values, period=14):

    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, period + 1):

        change = (
            values[i] - values[i - 1]
        )

        if change > 0:

            gains.append(change)
            losses.append(0)

        else:

            gains.append(0)
            losses.append(abs(change))

    avg_gain = (
        sum(gains) / period
    )

    avg_loss = (
        sum(losses) / period
    )

    for i in range(
        period + 1,
        len(values)
    ):

        change = (
            values[i] - values[i - 1]
        )

        gain = max(change, 0)
        loss = max(-change, 0)

        avg_gain = (
            (
                avg_gain * (period - 1)
                + gain
            )
            / period
        )

        avg_loss = (
            (
                avg_loss * (period - 1)
                + loss
            )
            / period
        )

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
    )


# =========================================================
# BOLLINGER BANDS
# =========================================================

def calculate_bollinger(
    values,
    period=20
):

    if len(values) < period:

        return None, None, None

    recent = values[-period:]

    middle = (
        sum(recent) / period
    )

    variance = sum(
        (price - middle) ** 2
        for price in recent
    ) / period

    standard_deviation = (
        variance ** 0.5
    )

    upper = (
        middle
        + 2 * standard_deviation
    )

    lower = (
        middle
        - 2 * standard_deviation
    )

    return (
        upper,
        middle,
        lower
    )


# =========================================================
# MACD
# =========================================================

def calculate_macd(values):

    if len(values) < 35:

        return None, None

    macd_values = []

    for i in range(
        26,
        len(values) + 1
    ):

        section = values[:i]

        ema12 = calculate_ema(
            section,
            12
        )

        ema26 = calculate_ema(
            section,
            26
        )

        if (
            ema12 is not None
            and ema26 is not None
        ):

            macd_values.append(
                ema12 - ema26
            )

    if len(macd_values) < 9:

        return None, None

    macd_line = macd_values[-1]

    signal_line = calculate_ema(
        macd_values,
        9
    )

    return (
        macd_line,
        signal_line
    )


# =========================================================
# CANDLE ANALYSIS
# =========================================================

def candle_direction(candle):

    if candle["close"] > candle["open"]:

        return "bullish"

    if candle["close"] < candle["open"]:

        return "bearish"

    return "neutral"


# =========================================================
# SIGNAL ANALYSIS
# =========================================================

def analyze_pair(
    pair_name,
    yahoo_symbol
):

    candles = get_candles(
        yahoo_symbol
    )

    if len(candles) < 40:

        print(
            f"{pair_name}: "
            f"not enough candles"
        )

        return None

    closes = [
        candle["close"]
        for candle in candles
    ]

    current_price = closes[-1]

    ema9 = calculate_ema(
        closes,
        9
    )

    ema21 = calculate_ema(
        closes,
        21
    )

    rsi = calculate_rsi(
        closes,
        14
    )

    upper, middle, lower = (
        calculate_bollinger(
            closes,
            20
        )
    )

    macd_line, macd_signal = (
        calculate_macd(
            closes
        )
    )

    if any(
        value is None
        for value in [
            ema9,
            ema21,
            rsi,
            upper,
            middle,
            lower,
            macd_line,
            macd_signal
        ]
    ):

        return None

    call_score = 0
    put_score = 0

    call_reasons = []
    put_reasons = []

    # =====================================================
    # EMA TREND
    # =====================================================

    if ema9 > ema21:

        call_score += 1

        call_reasons.append(
            "EMA 9 above EMA 21"
        )

    elif ema9 < ema21:

        put_score += 1

        put_reasons.append(
            "EMA 9 below EMA 21"
        )

    # =====================================================
    # RSI
    # =====================================================

    if 50 < rsi < 70:

        call_score += 1

        call_reasons.append(
            "RSI bullish zone"
        )

    elif 30 < rsi < 50:

        put_score += 1

        put_reasons.append(
            "RSI bearish zone"
        )

    # =====================================================
    # MACD
    # =====================================================

    if macd_line > macd_signal:

        call_score += 1

        call_reasons.append(
            "MACD bullish"
        )

    elif macd_line < macd_signal:

        put_score += 1

        put_reasons.append(
            "MACD bearish"
        )

    # =====================================================
    # BOLLINGER
    # =====================================================

    if current_price > middle:

        call_score += 1

        call_reasons.append(
            "Price above Bollinger middle"
        )

    elif current_price < middle:

        put_score += 1

        put_reasons.append(
            "Price below Bollinger middle"
        )

    # =====================================================
    # CANDLE MOMENTUM
    # =====================================================

    last_candle = candles[-1]

    direction = candle_direction(
        last_candle
    )

    if direction == "bullish":

        call_score += 1

        call_reasons.append(
            "Last candle bullish"
        )

    elif direction == "bearish":

        put_score += 1

        put_reasons.append(
            "Last candle bearish"
        )

    # =====================================================
    # SIGNAL DECISION
    # =====================================================

    if (
        call_score >= MIN_SCORE
        and call_score > put_score
    ):

        signal = "CALL"
        score = call_score
        reasons = call_reasons

    elif (
        put_score >= MIN_SCORE
        and put_score > call_score
    ):

        signal = "PUT"
        score = put_score
        reasons = put_reasons

    else:

        return None

    agreement = int(
        (score / 5) * 100
    )

    return {
        "pair": pair_name,
        "signal": signal,
        "score": score,
        "agreement": agreement,
        "price": current_price,
        "rsi": rsi,
        "ema9": ema9,
        "ema21": ema21,
        "macd": macd_line,
        "macd_signal": macd_signal,
        "reasons": reasons
    }


# =========================================================
# SIGNAL MESSAGE
# =========================================================

def make_signal_message(signal):

    if signal["signal"] == "CALL":

        direction = "🟢 CALL / UP"

    else:

        direction = "🔴 PUT / DOWN"

    reasons = "\n".join(
        "• " + reason
        for reason in signal["reasons"]
    )

    now = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    message = f"""
<b>👑 {BOT_NAME}</b>

━━━━━━━━━━━━━━━━━━

{direction}

💱 Pair: <b>{signal["pair"]}</b>

⏱ Timeframe: <b>1 Minute</b>
⌛ Expiry: <b>{EXPIRY}</b>

📊 Indicator Agreement:
<b>{signal["agreement"]}%</b>

🧠 Score:
<b>{signal["score"]}/5</b>

💰 Price:
<b>{signal["price"]:.5f}</b>

📈 EMA 9:
<b>{signal["ema9"]:.5f}</b>

📉 EMA 21:
<b>{signal["ema21"]:.5f}</b>

📊 RSI:
<b>{signal["rsi"]:.2f}</b>

<b>🔎 Analysis</b>
{reasons}

🕐 Signal:
<b>{now}</b>

━━━━━━━━━━━━━━━━━━

⚠️ <i>Analysis signal only.
No signal guarantees profit.</i>
"""

    return message


# =========================================================
# AUTOMATIC SIGNAL ENGINE
# =========================================================

def signal_engine():

    print("================================")
    print(
        f"{BOT_NAME} signal engine started"
    )
    print("================================")

    while True:

        try:

            if not subscribers:

                time.sleep(10)

                continue

            print(
                "Starting market analysis..."
            )

            for pair_name, yahoo_symbol in PAIRS.items():

                print(
                    f"Analyzing {pair_name}..."
                )

                result = analyze_pair(
                    pair_name,
                    yahoo_symbol
                )

                if result is None:

                    continue

                signal_key = (
                    result["signal"],
                    round(
                        result["price"],
                        5
                    )
                )

                if (
                    last_signal.get(
                        pair_name
                    )
                    == signal_key
                ):

                    continue

                last_signal[
                    pair_name
                ] = signal_key

                message = (
                    make_signal_message(
                        result
                    )
                )

                for chat_id in list(
                    subscribers
                ):

                    send_message(
                        chat_id,
                        message
                    )

                print(
                    f"SIGNAL: "
                    f"{pair_name} "
                    f"{result['signal']} "
                    f"{result['score']}/5"
                )

                time.sleep(2)

            print(
                "Analysis cycle completed."
            )

            time.sleep(45)

        except Exception as e:

            print(
                "Signal engine error:",
                e
            )

            time.sleep(15)


# =========================================================
# TELEGRAM LISTENER
# =========================================================

def telegram_listener():

    print("================================")
    print("Telegram listener started")
    print("================================")

    offset = None

    while True:

        try:

            params = {
                "timeout": 25
            }

            if offset is not None:

                params["offset"] = offset

            response = requests.get(
                f"https://api.telegram.org/"
                f"bot{TELEGRAM_TOKEN}/getUpdates",
                params=params,
                timeout=35
            )

            data = response.json()

            # =================================================
            # CHECK TELEGRAM RESPONSE
            # =================================================

            if not data.get("ok"):

                print(
                    "Telegram getUpdates error:",
                    data
                )

                time.sleep(5)

                continue

            # =================================================
            # PROCESS UPDATES
            # =================================================

            updates = data.get(
                "result",
                []
            )

            for update in updates:

                offset = (
                    update["update_id"]
                    + 1
                )

                message = update.get(
                    "message"
                )

                if not message:

                    continue

                chat_id = message[
                    "chat"
                ]["id"]

                text = message.get(
                    "text",
                    ""
                ).strip()

                print(
                    f"Telegram message "
                    f"from {chat_id}: {text}"
                )

                # =================================================
                # START
                # =================================================

                if text == "/start":

                    subscribers.add(
                        chat_id
                    )

                    print(
                        "Subscriber added:",
                        chat_id
                    )

                    send_message(
                        chat_id,
                        f"""
<b>👑 {BOT_NAME}</b>

✅ Bot successfully activated.

📊 Automatic signal analysis is ON.

⏱ Timeframe: 1 Minute
⌛ Expiry: 1 Minute

<b>Commands:</b>

/start - Start automatic signals
/stop - Stop automatic signals
/signal - Get current analysis
/pairs - Show available pairs
/status - Bot status

━━━━━━━━━━━━━━━━━━

⚠️ <i>Analysis signal only.
No signal guarantees profit.</i>
"""
                    )

                # =================================================
                # STOP
                # =================================================

                elif text == "/stop":

                    subscribers.discard(
                        chat_id
                    )

              
