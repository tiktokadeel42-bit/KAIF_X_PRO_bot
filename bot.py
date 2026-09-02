import os
import time
import asyncio
import logging
import tempfile
from io import BytesIO
from datetime import datetime

import requests
import matplotlib.pyplot as plt
from flask import Flask, request, jsonify

from pyquotex.stable_api import Quotex


# =========================================================
# CONFIG
# =========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
QX_EMAIL = os.getenv("QX_EMAIL", "").strip()
QX_PASSWORD = os.getenv("QX_PASSWORD", "").strip()

PORT = int(os.getenv("PORT", "10000"))

WEBHOOK_SECRET = os.getenv(
    "WEBHOOK_SECRET",
    "qx_signal_bot_2026"
)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)

app = Flask(__name__)


# =========================================================
# QX PAIRS
# =========================================================

OTC_PAIRS = [
    "EURUSD_otc",
    "GBPUSD_otc",
    "USDJPY_otc",
    "AUDUSD_otc",
    "USDCAD_otc",
    "USDCHF_otc",
    "EURGBP_otc",
    "EURJPY_otc",
    "GBPJPY_otc",
    "AUDJPY_otc",
    "CADJPY_otc",
    "CHFJPY_otc",
    "AUDCAD_otc",
    "AUDCHF_otc",
    "AUDNZD_otc",
    "CADCHF_otc",
    "EURAUD_otc",
    "EURCAD_otc",
    "EURCHF_otc",
    "EURNZD_otc",
    "GBPAUD_otc",
    "GBPCAD_otc",
    "GBPCHF_otc",
    "GBPNZD_otc",
    "NZDUSD_otc",
    "NZDJPY_otc",
]

LIVE_PAIRS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "USDCAD",
    "USDCHF",
    "EURGBP",
    "EURJPY",
    "GBPJPY",
    "AUDJPY",
    "CADJPY",
    "CHFJPY",
    "NZDUSD",
    "NZDJPY",
]

ALL_PAIRS = OTC_PAIRS + LIVE_PAIRS


# =========================================================
# TELEGRAM FUNCTIONS
# =========================================================

def telegram_request(method, data=None, files=None):
    url = f"{TELEGRAM_API}/{method}"

    try:
        response = requests.post(
            url,
            data=data,
            files=files,
            timeout=30
        )

        return response.json()

    except Exception as e:
        logger.exception("Telegram request error: %s", e)
        return None


def send_message(chat_id, text):
    return telegram_request(
        "sendMessage",
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
    )


def send_photo(chat_id, photo_bytes, caption):
    return telegram_request(
        "sendPhoto",
        data={
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": "HTML"
        },
        files={
            "photo": (
                "signal.png",
                photo_bytes,
                "image/png"
            )
        }
    )


# =========================================================
# WEBHOOK SETUP
# =========================================================

def setup_webhook():

    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN missing")
        return False

    if not WEBHOOK_URL:
        logger.error("WEBHOOK_URL missing")
        return False

    webhook = (
        f"{WEBHOOK_URL}/telegram/{WEBHOOK_SECRET}"
    )

    try:
        response = requests.post(
            f"{TELEGRAM_API}/setWebhook",
            json={
                "url": webhook,
                "drop_pending_updates": False
            },
            timeout=30
        )

        result = response.json()

        logger.info(
            "Webhook setup result: %s",
            result
        )

        return result.get("ok", False)

    except Exception as e:
        logger.exception(
            "Webhook setup failed: %s",
            e
        )

        return False


# =========================================================
# CANDLE HELPERS
# =========================================================

def normalize_candles(raw_candles):

    if isinstance(raw_candles, dict):
        candles = raw_candles.get("data", [])

    elif isinstance(raw_candles, list):
        candles = raw_candles

    else:
        candles = []

    result = []

    for candle in candles:

        try:
            open_price = float(
                candle.get("open")
            )

            close_price = float(
                candle.get("close")
            )

            high_price = float(
                candle.get("high")
            )

            low_price = float(
                candle.get("low")
            )

            candle_time = candle.get(
                "time",
                candle.get("from", 0)
            )

            result.append({
                "open": open_price,
                "close": close_price,
                "high": high_price,
                "low": low_price,
                "time": candle_time
            })

        except Exception:
            continue

    result.sort(
        key=lambda x: x["time"]
    )

    return result


def candle_direction(candle):

    if candle["close"] > candle["open"]:
        return "CALL"

    if candle["close"] < candle["open"]:
        return "PUT"

    return "NEUTRAL"


def candle_body(candle):

    return abs(
        candle["close"] -
        candle["open"]
    )


def candle_range(candle):

    return max(
        candle["high"] -
        candle["low"],
        0.00000001
    )


# =========================================================
# SIGNAL ANALYSIS
# =========================================================

def analyze_setup(candles):

    if len(candles) < 5:
        return None

    # Last 2 CLOSED candles
    c1 = candles[-3]
    c2 = candles[-2]

    direction1 = candle_direction(c1)
    direction2 = candle_direction(c2)

    body1 = candle_body(c1)
    body2 = candle_body(c2)

    range1 = candle_range(c1)
    range2 = candle_range(c2)

    body_ratio1 = body1 / range1
    body_ratio2 = body2 / range2

    score = 0
    signal = None
    reason = ""

    # -----------------------------------------------------
    # BULLISH MOMENTUM
    # -----------------------------------------------------

    if (
        direction1 == "CALL"
        and direction2 == "CALL"
    ):

        score += 35

        if body_ratio2 > 0.55:
            score += 20

        if c2["close"] > c1["close"]:
            score += 15

        signal = "CALL"

        reason = (
            "Bullish momentum detected "
            "from the last 2 closed candles."
        )

    # -----------------------------------------------------
    # BEARISH MOMENTUM
    # -----------------------------------------------------

    elif (
        direction1 == "PUT"
        and direction2 == "PUT"
    ):

        score += 35

        if body_ratio2 > 0.55:
            score += 20

        if c2["close"] < c1["close"]:
            score += 15

        signal = "PUT"

        reason = (
            "Bearish momentum detected "
            "from the last 2 closed candles."
        )

    # -----------------------------------------------------
    # REVERSAL POSSIBILITY
    # -----------------------------------------------------

    else:

        upper_wick = (
            c2["high"] -
            max(c2["open"], c2["close"])
        )

        lower_wick = (
            min(c2["open"], c2["close"]) -
            c2["low"]
        )

        if lower_wick > body2 * 1.5:

            signal = "CALL"
            score = 45

            reason = (
                "Lower wick rejection detected."
            )

        elif upper_wick > body2 * 1.5:

            signal = "PUT"
            score = 45

            reason = (
                "Upper wick rejection detected."
            )

    if signal is None:
        return None

    confidence = min(score, 95)

    if confidence < 50:
        return None

    return {
        "signal": signal,
        "confidence": confidence,
        "reason": reason,
        "candle1": c1,
        "candle2": c2
    }


# =========================================================
# QX CONNECTION + SCAN
# =========================================================

async def get_open_pairs(client):

    open_pairs = []

    # Check all configured OTC + LIVE pairs
    for pair in ALL_PAIRS:

        try:

            asset_name, asset_data = (
                await client.check_asset_open(pair)
            )

            if asset_data and len(asset_data) >= 3:

                is_open = bool(asset_data[2])

                if is_open:
                    open_pairs.append(
                        asset_name or pair
                    )

        except Exception:
            continue

    return open_pairs


async def get_pair_candles(client, pair):

    try:

        end_time = time.time()

        candles = await client.get_candles(
            pair,
            end_time,
            3600,
            60
        )

        return normalize_candles(candles)

    except Exception as e:

        logger.warning(
            "Candle error for %s: %s",
            pair,
            e
        )

        return []


async def scan_quotex():

    if not QX_EMAIL or not QX_PASSWORD:
        return {
            "error": (
                "QX_EMAIL or QX_PASSWORD "
                "is missing in Render Environment."
            )
        }

    client = None

    try:

        client = Quotex(
            email=QX_EMAIL,
            password=QX_PASSWORD,
            lang="en"
        )

        connected = await client.connect()

        # Different versions may return tuple
        if isinstance(connected, tuple):
            success = connected[0]
            message = connected[1]

        else:
            success = bool(connected)
            message = ""

        if not success:

            return {
                "error": (
                    f"QX connection failed: {message}"
                )
            }

        logger.info(
            "Connected to Quotex"
        )

        open_pairs = await get_open_pairs(client)

        logger.info(
            "Open pairs found: %s",
            len(open_pairs)
        )

        if not open_pairs:

            return {
                "error": "No configured pairs are open."
            }

        best_result = None

        # Limit scan to avoid excessive requests
        pairs_to_scan = open_pairs[:20]

        for pair in pairs_to_scan:

            candles = await get_pair_candles(
                client,
                pair
            )

            if not candles:
                continue

            analysis = analyze_setup(candles)

            if analysis is None:
                continue

            result = {
                "pair": pair,
                "candles": candles,
                **analysis
            }

            if (
                best_result is None
                or result["confidence"]
                > best_result["confidence"]
            ):

                best_result = result

        if best_result is None:

            return {
                "error": (
                    "No strong setup found right now. "
                    "Try again after the next candle."
                ),
                "open_pairs": len(open_pairs)
            }

        best_result["open_pairs"] = len(open_pairs)

        return best_result

    except Exception as e:

        logger.exception(
            "QX scan error"
        )

        return {
            "error": str(e)
        }

    finally:

        if client is not None:

            try:
                await client.close()

            except Exception:
                pass


# =========================================================
# CHART IMAGE
# =========================================================

def create_signal_chart(result):

    c1 = result["candle1"]
    c2 = result["candle2"]

    signal = result["signal"]

    candles = [c1, c2]

    # Projected third candle
    last_close = c2["close"]

    average_range = (
        candle_range(c1) +
        candle_range(c2)
    ) / 2

    projected_size = average_range * 0.55

    if signal == "CALL":

        projected_open = last_close
        projected_close = (
            last_close + projected_size
        )

        projected_high = (
            projected_close +
            projected_size * 0.25
        )

        projected_low = (
            projected_open -
            projected_size * 0.20
        )

    else:

        projected_open = last_close
        projected_close = (
            last_close - projected_size
        )

        projected_high = (
            projected_open +
            projected_size * 0.20
        )

        projected_low = (
            projected_close -
            projected_size * 0.25
        )

    projected = {
        "open": projected_open,
        "close": projected_close,
        "high": projected_high,
        "low": projected_low
    }

    plt.figure(figsize=(8, 6))

    all_items = candles + [projected]

    labels = [
        "Candle 1",
        "Candle 2",
        "Projected 3"
    ]

    for i, candle in enumerate(all_items):

        x = i + 1

        plt.plot(
            [x, x],
            [
                candle["low"],
                candle["high"]
            ],
            linewidth=2
        )

        body_bottom = min(
            candle["open"],
            candle["close"]
        )

        body_height = abs(
            candle["close"] -
            candle["open"]
        )

        if body_height == 0:
            body_height = (
                candle_range(candle) * 0.05
            )

        rect = plt.Rectangle(
            (
                x - 0.25,
                body_bottom
            ),
            0.5,
            body_height,
            alpha=0.7
        )

        plt.gca().add_patch(rect)

    plt.xticks(
        [1, 2, 3],
        labels
    )

    plt.title(
        f"{result['pair']} | "
        f"{signal} | "
        f"Confidence {result['confidence']}%"
    )

    plt.ylabel("Price")

    plt.grid(
        True,
        alpha=0.25
    )

    plt.tight_layout()

    buffer = BytesIO()

    plt.savefig(
        buffer,
        format="png",
        dpi=150
    )

    plt.close()

    buffer.seek(0)

    return buffer.getvalue()


# =========================================================
# HANDLE TELEGRAM COMMANDS
# =========================================================

def handle_start(chat_id):

    text = (
        "🤖 <b>QX SIGNAL BOT ONLINE</b>\n\n"
        "📊 Market: Quotex OTC + Live\n"
        "⏱ Timeframe: 1 Minute\n\n"
        "Commands:\n"
        "▶️ /signal - Scan best setup"
    )

    send_message(
        chat_id,
        text
    )


def handle_signal(chat_id):

    send_message(
        chat_id,
        "🔍 <b>Scanning QX OTC + Live market...</b>\n"
        "Please wait..."
    )

    try:

        result = asyncio.run(
            scan_quotex()
        )

    except Exception as e:

        logger.exception(
            "Async scan error"
        )

        send_message(
            chat_id,
            f"❌ Scan error: {e}"
        )

        return

    if result.get("error"):

        send_message(
            chat_id,
            (
                "⚠️ <b>No signal available</b>\n\n"
                f"{result['error']}"
            )
        )

        return

    image = create_signal_chart(result)

    market_type = (
        "OTC"
        if "_otc" in result["pair"].lower()
        else "LIVE"
    )

    caption = (
        f"📊 <b>QX BEST SIGNAL</b>\n\n"
        f"💱 Pair: <b>{result['pair']}</b>\n"
        f"🌐 Market: <b>{market_type}</b>\n"
        f"⏱ Timeframe: <b>1 Minute</b>\n"
        f"🎯 Signal: <b>{result['signal']}</b>\n"
        f"📈 Confidence: "
        f"<b>{result['confidence']}%</b>\n\n"
        f"📝 {result['reason']}\n\n"
        f"🟢 Chart shows the last 2 closed candles "
        f"and a projected next-candle structure.\n\n"
        f"⚠️ Projection is analysis, not a guarantee."
    )

    send_photo(
        chat_id,
        image,
        caption
    )


def process_update(update):

    message = update.get("message")

    if not message:
        return

    chat = message.get("chat", {})

    chat_id = chat.get("id")

    text = message.get(
        "text",
        ""
    ).strip()

    if not chat_id:
        return

    if text.startswith("/start"):

        handle_start(chat_id)

    elif text.startswith("/signal"):

        handle_signal(chat_id)

    else:

        send_message(
            chat_id,
            "Use /start or /signal"
        )


# =========================================================
# FLASK ROUTES
# =========================================================

@app.route("/", methods=["GET"])
def home():

    return jsonify({
        "status": "online",
        "bot": "QX Signal Bot",
        "market": "OTC + Live"
    })


@app.route("/health", methods=["GET"])
def health():

    return jsonify({
        "status": "healthy"
    })


@app.route(
    f"/telegram/{WEBHOOK_SECRET}",
    methods=["POST"]
)
def telegram_webhook():

    try:

        update = request.get_json(
            force=True,
            silent=True
        )

        if update:
            process_update(update)

        return jsonify({
            "ok": True
        })

    except Exception as e:

        logger.exception(
            "Webhook processing error"
        )

        return jsonify({
            "ok": False,
            "error": str(e)
        }), 200


# =========================================================
# STARTUP
# =========================================================

try:
    setup_webhook()

except Exception as e:
    logger.warning(
        "Initial webhook setup failed: %s",
        e
    )


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False
      )
