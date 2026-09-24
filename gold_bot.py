import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import mplfinance as mpf

CONFIG_PATH = Path(__file__).parent / "config.json"
CANDLE_INTERVAL = "5m"
CANDLE_COUNT = 100
SWING_ORDER = 3  # candles on each side that must be lower/higher to count as a swing point
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


def fetch_intraday_prices(interval=CANDLE_INTERVAL, range_param="5d"):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
    resp = requests.get(
        url,
        params={"range": range_param, "interval": interval},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    df = pd.DataFrame({
        "Date": pd.to_datetime(result["timestamp"], unit="s"),
        "Open": quote["open"],
        "High": quote["high"],
        "Low": quote["low"],
        "Close": quote["close"],
        "Volume": quote.get("volume", [0] * len(result["timestamp"])),
    }).dropna(subset=["Open", "High", "Low", "Close"])
    df = df.sort_values("Date").tail(CANDLE_COUNT).set_index("Date")
    if df.empty:
        raise RuntimeError("Yahoo Finance returned no intraday gold price data")
    return df


def make_chart(df, out_path):
    # TradingView-style dark candlestick chart, M15 candles.
    market_colors = mpf.make_marketcolors(
        up="#26a69a", down="#ef5350",
        edge="inherit", wick="inherit", volume="in",
    )
    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=market_colors,
        facecolor="#131722", figcolor="#131722", edgecolor="#131722",
        gridcolor="#2a2e39", gridstyle="--",
        rc={"axes.labelcolor": "#d1d4dc", "xtick.color": "#d1d4dc", "ytick.color": "#d1d4dc"},
    )
    num = "".join(c for c in CANDLE_INTERVAL if c.isdigit())
    unit = "".join(c for c in CANDLE_INTERVAL if c.isalpha()).upper()
    label = f"{unit}{num}"
    mpf.plot(
        df, type="candle", style=style,
        title=f"\nGold (XAU/USD futures) — {label}, last {len(df)} candles",
        ylabel="Price (USD)",
        figsize=(10, 5.5),
        savefig=dict(fname=str(out_path), dpi=150),
    )


def find_swings(df, order=SWING_ORDER):
    """Local swing highs/lows: a candle whose High/Low is the extreme within its +/-order window."""
    highs, lows = df["High"].values, df["Low"].values
    swing_highs, swing_lows = [], []
    for i in range(order, len(df) - order):
        window = slice(i - order, i + order + 1)
        if highs[i] == highs[window].max():
            swing_highs.append((i, highs[i]))
        if lows[i] == lows[window].min():
            swing_lows.append((i, lows[i]))
    return swing_highs, swing_lows


def summarize_trend(df):
    """Price-action read of the candles actually shown on the chart: market structure
    (higher highs/lows vs lower highs/lows), momentum, the character of the latest candle,
    and the nearest support/resistance."""
    latest = df.iloc[-1]
    is_bullish = latest["Close"] >= latest["Open"]
    candle_range = latest["High"] - latest["Low"]
    body = abs(latest["Close"] - latest["Open"])
    body_ratio = body / candle_range if candle_range > 0 else 0
    if body_ratio >= 0.6:
        candle_strength = "strong"
    elif body_ratio <= 0.25:
        candle_strength = "indecisive"
    else:
        candle_strength = "moderate"

    # Market structure from the last two swing highs and lows.
    swing_highs, swing_lows = find_swings(df)
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        higher_highs = swing_highs[-1][1] > swing_highs[-2][1]
        higher_lows = swing_lows[-1][1] > swing_lows[-2][1]
        if higher_highs and higher_lows:
            structure = "Uptrend (higher highs & higher lows)"
        elif not higher_highs and not higher_lows:
            structure = "Downtrend (lower highs & lower lows)"
        else:
            structure = "Ranging / mixed structure"
    else:
        structure = "Not enough swings yet to read structure"

    # Momentum: consecutive candles closing the same direction, most recent first.
    directions = (df["Close"] >= df["Open"]).values
    streak = 1
    for i in range(len(directions) - 1, 0, -1):
        if directions[i] == directions[i - 1]:
            streak += 1
        else:
            break
    momentum_word = "bullish" if directions[-1] else "bearish"
    momentum = (
        f"{streak} consecutive {momentum_word} candles" if streak >= 2
        else "no clear momentum (last candle flipped direction)"
    )

    # Support/resistance from the recent range, and whether price just broke out of it.
    lookback = df.iloc[:-1].tail(30)
    resistance = lookback["High"].max()
    support = lookback["Low"].min()
    near_pct = 0.0015  # ~0.15% counts as "testing" a level
    if latest["Close"] > resistance:
        level_note = f"Broke above resistance (${resistance:.2f})"
    elif latest["Close"] < support:
        level_note = f"Broke below support (${support:.2f})"
    elif latest["Close"] >= resistance * (1 - near_pct):
        level_note = f"Testing resistance (${resistance:.2f})"
    elif latest["Close"] <= support * (1 + near_pct):
        level_note = f"Testing support (${support:.2f})"
    else:
        level_note = f"Inside range (${support:.2f} - ${resistance:.2f})"

    return (
        f"Gold {CANDLE_INTERVAL.upper()} price action: ${latest['Close']:.2f}\n"
        f"Structure: {structure}\n"
        f"Momentum: {momentum}\n"
        f"Last candle: {'Bullish' if is_bullish else 'Bearish'}, {candle_strength} (body {body_ratio*100:.0f}% of range)\n"
        f"{level_note}"
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
    intraday_df = fetch_intraday_prices()

    chart_path = Path(__file__).parent / "latest_chart.png"
    make_chart(intraday_df, chart_path)

    summary = summarize_trend(intraday_df)
    image_url = upload_image(config["imgbb_api_key"], chart_path)
    send_to_line(config["line_channel_access_token"], config["line_group_id"], image_url, summary)

    bkk_now = datetime.now(timezone(timedelta(hours=7)))
    print(f"[{bkk_now.isoformat()}] Sent gold update to LINE group.")


if __name__ == "__main__":
    main()
