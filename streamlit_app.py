"""Streamlit entry point for the Fieldwise dashboard."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import streamlit as st
import streamlit.components.v1 as components

PROJECT_DIR = Path(__file__).resolve().parent
FRONTEND_FILE = PROJECT_DIR / "spatial x" / "public" / "sahil.html"
CONFIG = {
    "CHANNEL_ID_SECRET": "THINGSPEAK_CHANNEL_ID",
    "CHANNEL_ID_DEFAULT": "3517806",
    "READ_KEY_SECRET": "THINGSPEAK_READ_API_KEY",
    "FIELD_MAP": {
        "soil": 1,
        "water": 2,
        "fire": 3,
        "humidity": 4,
        "temperature": 5,
    },
    "SOIL_DRY": 310.0,  # Provisional raw value; calibrate in dry soil.
    "SOIL_WET": 300.0,  # Provisional raw value; calibrate in wet soil.
    "WATER_MAX": 4095.0,
    "FIRE_THRESHOLD": 250.0,  # Provisional; verify against the actual sensor.
    "FIRE_ACTIVE_WHEN": "above",  # Set to "below" if the sensor works oppositely.
    "SOIL_ALERT_THRESHOLD": 30.0,
    "LOW_WATER_ALERT_THRESHOLD": 20.0,
    "REFRESH_SECONDS": 15,
    "RESULTS": 30,
}
DATA_PLACEHOLDER = "__FIELDWISE_STREAMLIT_DATA_JSON__"

st.set_page_config(page_title="Fieldwise", layout="wide")
st.markdown(
    """
    <style>
      #MainMenu, header, footer, [data-testid="stHeader"],
      [data-testid="stToolbar"] { visibility: hidden; height: 0; }
      .block-container { max-width: 100%; padding: 0; }
      iframe { width: 100% !important; border: 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

def get_secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default)).strip()
    except (KeyError, FileNotFoundError):
        return default


def parse_thingspeak_feeds(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("feeds"), list):
        raise ValueError("ThingSpeak returned an invalid feeds response.")
    if CONFIG["SOIL_DRY"] == CONFIG["SOIL_WET"]:
        raise ValueError("Soil DRY and WET calibration values must differ.")
    if CONFIG["WATER_MAX"] <= 0:
        raise ValueError("WATER_MAX must be greater than zero.")
    if CONFIG["FIRE_ACTIVE_WHEN"] not in {"below", "above"}:
        raise ValueError("Fire active direction must be 'below' or 'above'.")

    readings = []
    for feed in payload["feeds"]:
        if not isinstance(feed, dict):
            continue
        fields = CONFIG["FIELD_MAP"]

        def read_field(name: str) -> float | None:
            raw_value = feed.get(f"field{fields[name]}")
            if raw_value in (None, ""):
                return None
            try:
                value = float(raw_value)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"ThingSpeak field{fields[name]} ({name}) must be numeric."
                ) from error
            if not math.isfinite(value):
                raise ValueError(
                    f"ThingSpeak field{fields[name]} ({name}) is not finite."
                )
            return value

        soil_raw = read_field("soil")
        water_raw = read_field("water")
        if soil_raw is None or water_raw is None:
            continue

        temperature = read_field("temperature")
        humidity = read_field("humidity")
        fire_raw = read_field("fire")
        if temperature is None or temperature == 1:
            temperature = None
        if humidity is None or humidity == 1:
            humidity = None

        soil_percent = (
            (CONFIG["SOIL_DRY"] - soil_raw)
            / (CONFIG["SOIL_DRY"] - CONFIG["SOIL_WET"])
            * 100
        )
        water_percent = water_raw / CONFIG["WATER_MAX"] * 100
        fire_threshold = CONFIG["FIRE_THRESHOLD"]
        fire = fire_raw is not None and (
            fire_raw < fire_threshold
            if CONFIG["FIRE_ACTIVE_WHEN"] == "below"
            else fire_raw > fire_threshold
        )

        readings.append(
            {
                "soil": min(100.0, max(0.0, soil_percent)),
                "temperature": temperature,
                "humidity": humidity,
                "waterLevel": min(100.0, max(0.0, water_percent)),
                "waterSensorCheck": water_raw >= CONFIG["WATER_MAX"],
                "fire": fire,
                "fireRaw": fire_raw,
                "timestamp": feed.get("created_at"),
            }
        )

    if not readings:
        raise ValueError(
            "ThingSpeak has no complete readings in fields 1–5 yet. "
            "Check that your channel is public and has data in all five fields."
        )
    return readings


def fetch_thingspeak_readings() -> list[dict[str, Any]]:
    channel_id = get_secret(
        CONFIG["CHANNEL_ID_SECRET"], CONFIG["CHANNEL_ID_DEFAULT"]
    )
    if not channel_id:
        raise ValueError("Set THINGSPEAK_CHANNEL_ID in Streamlit app secrets.")
    url = (
        f"https://api.thingspeak.com/channels/{channel_id}/feeds.json"
    )
    params: dict[str, int | str] = {"results": CONFIG["RESULTS"]}
    api_key = get_secret(CONFIG["READ_KEY_SECRET"])
    if api_key:
        params["api_key"] = api_key

    try:
        response = requests.get(url, params=params, timeout=10)
    except requests.RequestException as error:
        raise ValueError(f"Could not reach ThingSpeak: {error}") from error
    if not response.ok:
        raise ValueError(
            f"ThingSpeak returned HTTP {response.status_code}. "
            "Check the channel ID and its public/read-key access."
        )
    try:
        payload = response.json()
    except requests.exceptions.JSONDecodeError as error:
        raise ValueError("ThingSpeak returned invalid JSON.") from error
    return parse_thingspeak_feeds(payload)


def build_dashboard_html(readings: list[dict[str, Any]]) -> str:
    html = FRONTEND_FILE.read_text(encoding="utf-8")
    if html.count(DATA_PLACEHOLDER) != 1:
        raise ValueError("Dashboard must contain exactly one data placeholder.")
    payload = {
        "live": bool(readings),
        "lastUpdated": (
            readings[-1]["timestamp"]
            if readings and readings[-1]["timestamp"]
            else datetime.now(timezone.utc).isoformat()
        ),
        "readings": readings,
        "config": {
            "refreshIntervalMs": CONFIG["REFRESH_SECONDS"] * 1000,
            "soilMoistureThreshold": CONFIG["SOIL_ALERT_THRESHOLD"],
            "lowWaterThreshold": CONFIG["LOW_WATER_ALERT_THRESHOLD"],
        },
    }
    serialized_payload = json.dumps(payload).replace("</", "<\\/")
    return html.replace(DATA_PLACEHOLDER, serialized_payload)


@st.fragment(run_every=CONFIG["REFRESH_SECONDS"])
def render_dashboard() -> None:
    try:
        readings = fetch_thingspeak_readings()
    except ValueError as error:
        st.warning(f"ThingSpeak unavailable; showing demo readings. {error}")
        readings = []
    components.html(build_dashboard_html(readings), height=2200, scrolling=True)


render_dashboard()
