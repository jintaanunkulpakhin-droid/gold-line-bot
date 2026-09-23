import base64
import hashlib
import hmac
from pathlib import Path

from flask import Flask, request, abort

from gold_bot import (
    load_config,
    fetch_gold_prices,
    make_chart,
    summarize_trend,
    upload_image,
)

TRIGGER_WORD = "gold"

app = Flask(__name__)
config = load_config()


def verify_signature(body: bytes, signature: str) -> bool:
    mac = hmac.new(config["line_channel_secret"].encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode()
    return hmac.compare_digest(expected, signature or "")


def reply_to_line(reply_token, image_url, summary_text):
    import requests

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
            df = fetch_gold_prices()
            chart_path = Path(__file__).parent / "latest_chart.png"
            make_chart(df, chart_path)
            summary = summarize_trend(df)
            image_url = upload_image(config["imgbb_api_key"], chart_path)
            reply_to_line(event["replyToken"], image_url, summary)
        except Exception as exc:
            print(f"Failed to handle trigger event: {exc}")

    return "OK"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
