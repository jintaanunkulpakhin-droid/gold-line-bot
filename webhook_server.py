import base64
import hashlib
import hmac
import json
import os
from pathlib import Path

import requests
from flask import Flask, request, abort

from gold_bot import (
    load_config,
    fetch_intraday_prices,
    make_chart,
    summarize_trend,
    upload_image,
    CONFIG_PATH,
)

TRIGGER_WORD = "gold"

app = Flask(__name__)
config = load_config()


def get_telegram_config():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if token and secret:
        return token, secret
    if CONFIG_PATH.exists():
        local = json.loads(CONFIG_PATH.read_text())
        return local.get("telegram_bot_token"), local.get("telegram_webhook_secret")
    return None, None


def verify_signature(body: bytes, signature: str) -> bool:
    mac = hmac.new(config["line_channel_secret"].encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode()
    return hmac.compare_digest(expected, signature or "")


def reply_to_line(reply_token, image_url, summary_text):
    resp = requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers={
            "Authorization": f"Bearer {config['line_channel_access_token']}",
            "Content-Type": "application/json",
        },
        json={
            "replyToken": reply_token,
            "messages": [
                {"type": "image", "originalContentUrl": image_url, "previewImageUrl": image_url},
                {"type": "text", "text": summary_text},
            ],
        },
        timeout=15,
    )
    resp.raise_for_status()


@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data()
    if not verify_signature(body, request.headers.get("X-Line-Signature")):
        abort(400)

    for event in request.get_json().get("events", []):
        if event.get("type") in ("join", "leave", "memberLeft"):
            print(f"[{event['type']}] source={event.get('source')} timestamp={event.get('timestamp')}")
        if event.get("type") != "message":
            continue
        if event.get("deliveryContext", {}).get("isRedelivery"):
            continue
        message = event.get("message", {})
        if message.get("type") != "text":
            continue
        if TRIGGER_WORD not in message.get("text", "").lower():
            continue

        try:
            intraday_df = fetch_intraday_prices()
            chart_path = Path(__file__).parent / "latest_chart.png"
            make_chart(intraday_df, chart_path)
            summary = summarize_trend(intraday_df)
            image_url = upload_image(config["imgbb_api_key"], chart_path)
            reply_to_line(event["replyToken"], image_url, summary)
        except Exception as exc:
            print(f"Failed to handle trigger event: {exc}")

    return "OK"


def send_photo_telegram(token, chat_id, image_path, caption):
    with open(image_path, "rb") as f:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": f},
            timeout=30,
        )
    resp.raise_for_status()


@app.route("/telegram_callback", methods=["POST"])
def telegram_callback():
    token, secret = get_telegram_config()
    if not token or not secret:
        abort(503)
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
        abort(403)

    update = request.get_json(silent=True) or {}
    message = update.get("message") or update.get("edited_message") or {}
    text = message.get("text", "")
    if TRIGGER_WORD not in text.lower():
        return "OK"

    try:
        intraday_df = fetch_intraday_prices()
        chart_path = Path(__file__).parent / "latest_chart_tg.png"
        make_chart(intraday_df, chart_path)
        summary = summarize_trend(intraday_df)
        send_photo_telegram(token, message["chat"]["id"], chart_path, summary)
    except Exception as exc:
        print(f"Failed to handle Telegram trigger event: {exc}")

    return "OK"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
