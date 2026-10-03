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
THINGSPEAK_CHANNEL_ID = "3517806"
THINGSPEAK_RESULTS = 20
THINGSPEAK_REFRESH_SECONDS = 15

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

    readings = []
    for feed in payload["feeds"]:
        if not isinstance(feed, dict):
            continue
        values = [feed.get(f"field{number}") for number in range(1, 6)]
        if any(value in (None, "") for value in values):
            continue

        try:
            soil, temperature, humidity, water_level = (
                float(value) for value in values[:4]
            )
        except (TypeError, ValueError) as error:
            raise ValueError(
                "ThingSpeak fields 1–4 must contain numeric sensor readings."
            ) from error
        if not all(
            math.isfinite(value)
            for value in (soil, temperature, humidity, water_level)
        ):
            raise ValueError("ThingSpeak returned a non-finite sensor value.")

        fire_value = str(values[4]).strip().lower()
        if fire_value in {"1", "true", "yes", "on"}:
            fire = True
        elif fire_value in {"0", "false", "no", "off"}:
            fire = False
        else:
            raise ValueError(
                "ThingSpeak field5 must contain a boolean-like value "
                "(0/1, true/false, yes/no, or on/off)."
            )

        readings.append(
            {
                "soil": soil,
                "temperature": temperature,
                "humidity": humidity,
                "waterLevel": water_level,
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
        f"{THINGSPEAK_CHANNEL_ID}/feeds.json"
    )
    params: dict[str, int | str] = {"results": THINGSPEAK_RESULTS}
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


@st.fragment(run_every=THINGSPEAK_REFRESH_SECONDS)
def render_dashboard() -> None:
    try:
        readings = fetch_thingspeak_readings()
    except ValueError as error:
        st.error(f"ThingSpeak connection failed: {error}")
        readings = []
    else:
        st.caption(
            f"Connected to ThingSpeak channel {THINGSPEAK_CHANNEL_ID}; "
            f"refreshing every {THINGSPEAK_REFRESH_SECONDS} seconds."
        )
    components.html(build_dashboard_html(readings), height=2200, scrolling=True)


render_dashboard()
