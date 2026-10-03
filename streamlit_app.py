"""Streamlit entry point for the Fieldwise dashboard."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import requests
import streamlit as st
import streamlit.components.v1 as components

PROJECT_DIR = Path(__file__).resolve().parent
FRONTEND_FILE = PROJECT_DIR / "spatial x" / "public" / "sahil.html"
THINGSPEAK_CONFIG = {
    "channel_id": "3517806",
    "fields": {
        "soil": 1,
        "water": 2,
        "fire": 3,
        "humidity": 4,
        "temperature": 5,
    },
    "soil_dry_raw": 310.0,  # Provisional; calibrate with dry soil.
    "soil_wet_raw": 300.0,  # Provisional; calibrate with wet soil.
    "water_adc_max": 4095.0,
    "water_check_raw": 4095.0,
    "fire_threshold_raw": 180.0,
    "fire_active_when": "below",
    "results": 20,
    "refresh_seconds": 15,
}

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

def get_thingspeak_read_api_key() -> str:
    try:
        return str(st.secrets["THINGSPEAK_READ_API_KEY"]).strip()
    except (KeyError, FileNotFoundError):
        return ""


def parse_thingspeak_feeds(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("feeds"), list):
        raise ValueError("ThingSpeak returned an invalid feeds response.")
    if THINGSPEAK_CONFIG["soil_dry_raw"] == THINGSPEAK_CONFIG["soil_wet_raw"]:
        raise ValueError("Soil DRY and WET calibration values must differ.")
    if THINGSPEAK_CONFIG["water_adc_max"] <= 0:
        raise ValueError("The water ADC maximum must be greater than zero.")
    if THINGSPEAK_CONFIG["fire_active_when"] not in {"below", "above"}:
        raise ValueError("Fire active direction must be 'below' or 'above'.")

    readings = []
    for feed in payload["feeds"]:
        if not isinstance(feed, dict):
            continue
        fields = THINGSPEAK_CONFIG["fields"]

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
        if temperature == 1:
            temperature = None
        if humidity == 1:
            humidity = None

        dry = THINGSPEAK_CONFIG["soil_dry_raw"]
        wet = THINGSPEAK_CONFIG["soil_wet_raw"]
        soil_percent = (dry - soil_raw) / (dry - wet) * 100
        water_percent = water_raw / THINGSPEAK_CONFIG["water_adc_max"] * 100
        fire_threshold = THINGSPEAK_CONFIG["fire_threshold_raw"]
        fire = fire_raw is not None and (
            fire_raw < fire_threshold
            if THINGSPEAK_CONFIG["fire_active_when"] == "below"
            else fire_raw > fire_threshold
        )

        readings.append(
            {
                "soil": min(100.0, max(0.0, soil_percent)),
                "temperature": temperature,
                "humidity": humidity,
                "waterLevel": min(100.0, max(0.0, water_percent)),
                "waterSensorCheck": water_raw >= THINGSPEAK_CONFIG["water_check_raw"],
                "fire": fire,
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
    url = (
        f"https://api.thingspeak.com/channels/"
        f"{THINGSPEAK_CONFIG['channel_id']}/feeds.json"
    )
    params: dict[str, int | str] = {"results": THINGSPEAK_CONFIG["results"]}
    api_key = get_thingspeak_read_api_key()
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
    html = html.replace(
        "const THINGSPEAK_READINGS = [];",
        f"const THINGSPEAK_READINGS = {json.dumps(readings)};",
    )
    if readings:
        html = html.replace(
            'dashboardEyebrow: "Local simulation"',
            'dashboardEyebrow: "ThingSpeak live data"',
        ).replace(
            'dashboardIntro: "Sensor values refresh every three seconds. This demo uses realistic simulated data; it is not connected to physical sensors."',
            'dashboardIntro: "Live sensor readings from ThingSpeak refresh every fifteen seconds."',
        ).replace(
            'simulatedLive: "SIMULATED LIVE"',
            'simulatedLive: "THINGSPEAK LIVE"',
        ).replace(
            'simulationNote: "Simulated values · No hardware connection"',
            'simulationNote: "ThingSpeak sensor feed · Pump control remains demo-only"',
        )
    return html


@st.fragment(run_every=THINGSPEAK_CONFIG["refresh_seconds"])
def render_dashboard() -> None:
    try:
        readings = fetch_thingspeak_readings()
    except ValueError as error:
        st.error(f"ThingSpeak connection failed: {error}")
        readings = []
    else:
        st.caption(
            f"Connected to ThingSpeak channel {THINGSPEAK_CONFIG['channel_id']}; "
            f"refreshing every {THINGSPEAK_CONFIG['refresh_seconds']} seconds."
        )
    components.html(build_dashboard_html(readings), height=2200, scrolling=True)


render_dashboard()
