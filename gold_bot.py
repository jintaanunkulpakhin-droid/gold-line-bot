import json
import io
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

CONFIG_PATH = Path(__file__).parent / "config.json"
CHART_DAYS = 90
CONFIG_KEYS = [
    "line_channel_access_token",
    "line_group_id",
    "imgbb_api_key",
    "line_channel_secret",
]


def load_config():
    # Env vars take priority (used on Render/Railway); config.json is the local-dev fallback.
    if all(os.environ.get(k.upper()) for k in CONFIG_KEYS):
        return {k: os.environ[k.upper()] for k in CONFIG_KEYS}

    config = json.loads(CONFIG_PATH.read_text())
    missing = [k for k, v in config.items() if not v]
    if missing:
        raise RuntimeError(f"config.json is missing values for: {', '.join(missing)}")
    return config


def fetch_gold_prices():
    url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
    resp = requests.get(
        url,
        params={"range": "6mo", "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    df = pd.DataFrame({
        "Date": pd.to_datetime(result["timestamp"], unit="s").normalize(),
        "Open": quote["open"],
        "High": quote["high"],
        "Low": quote["low"],
        "Close": quote["close"],
    }).dropna()
    df = df.sort_values("Date").tail(CHART_DAYS).reset_index(drop=True)
    if df.empty:
        raise RuntimeError("Yahoo Finance returned no gold price data")
    return df


def make_chart(df, out_path):
    df = df.copy()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA50"] = df["Close"].rolling(50).mean()

    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=150)
    ax.plot(df["Date"], df["Close"], label="XAU/USD", color="#c9a227", linewidth=2)
    ax.plot(df["Date"], df["MA20"], label="MA20", color="#4a90d9", linewidth=1, linestyle="--")
    ax.plot(df["Date"], df["MA50"], label="MA50", color="#9b59b6", linewidth=1, linestyle="--")
    ax.set_title(f"Gold (XAU/USD futures) — last {CHART_DAYS} trading days")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def summarize_trend(df):
    # PLACEHOLDER — replace with your own trend rules.
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    day_change_pct = (latest["Close"] - prev["Close"]) / prev["Close"] * 100
    week = df.tail(7)
    week_change_pct = (latest["Close"] - week.iloc[0]["Close"]) / week.iloc[0]["Close"] * 100
    window_high = df["High"].max()
    window_low = df["Low"].min()

    return (
        f"Gold {latest['Date'].strftime('%d %b %Y')}: ${latest['Close']:.2f}\n"
        f"Day: {day_change_pct:+.2f}%  |  7d: {week_change_pct:+.2f}%\n"
        f"{CHART_DAYS}d range: ${window_low:.2f} - ${window_high:.2f}"
    )


def upload_image(imgbb_api_key, image_path):
    with open(image_path, "rb") as f:
        resp = requests.post(
            "https://api.imgbb.com/1/upload",
            params={"key": imgbb_api_key},
            files={"image": f},
            timeout=30,
        )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"imgbb upload failed: {data}")
    return data["data"]["url"]


def send_to_line(channel_access_token, group_id, image_url, summary_text):
    resp = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {channel_access_token}",
            "Content-Type": "application/json",
        },
        json={
            "to": group_id,
            "messages": [
                {
                    "type": "image",
                    "originalContentUrl": image_url,
                    "previewImageUrl": image_url,
                },
                {"type": "text", "text": summary_text},
            ],
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"LINE push failed ({resp.status_code}): {resp.text}")


def main():
    config = load_config()
    df = fetch_gold_prices()

    chart_path = Path(__file__).parent / "latest_chart.png"
    make_chart(df, chart_path)

    summary = summarize_trend(df)
    image_url = upload_image(config["imgbb_api_key"], chart_path)
    send_to_line(config["line_channel_access_token"], config["line_group_id"], image_url, summary)

    bkk_now = datetime.now(timezone(timedelta(hours=7)))
    print(f"[{bkk_now.isoformat()}] Sent gold update to LINE group.")


if __name__ == "__main__":
    main()
