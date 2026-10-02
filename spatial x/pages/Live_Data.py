"""File-backed and simulated live sensor readings for Fieldwise."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

PROJECT_DIR = Path(__file__).resolve().parents[1]
READINGS_FILE = PROJECT_DIR / "python_backend" / "data" / "readings.json"
SENSOR_FIELDS = ("soil", "temperature", "humidity", "waterLevel")

# Generate plausible samples for local previews when the JSON store is empty.
def make_simulated_reading(timestamp: datetime | None = None) -> dict[str, Any]:
    return {
        "soil": random.randint(22, 72),
        "temperature": round(random.uniform(19, 43), 1),
        "humidity": random.randint(40, 88),
        "waterLevel": random.randint(12, 96),
        "fire": random.random() < 0.02,
        "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
    }


def get_simulated_readings() -> list[dict[str, Any]]:
    if "fieldwise_demo_readings" not in st.session_state:
        now = datetime.now(timezone.utc)
        st.session_state.fieldwise_demo_readings = [
            make_simulated_reading(now - timedelta(seconds=5 * index))
            for index in range(49, -1, -1)
        ]
    else:
        history = st.session_state.fieldwise_demo_readings
        history.append(make_simulated_reading())
        st.session_state.fieldwise_demo_readings = history[-50:]
    return st.session_state.fieldwise_demo_readings


# Read valid sensor rows, falling back to session-local demo history if needed.
def load_readings() -> tuple[list[dict[str, Any]], bool]:
    try:
        stored = json.loads(READINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        stored = []

    valid_rows = []
    if isinstance(stored, list):
        for item in stored:
            if not isinstance(item, dict):
                continue
            if any(
                isinstance(item.get(field), bool)
                or not isinstance(item.get(field), (int, float))
                for field in SENSOR_FIELDS
            ) or not isinstance(item.get("fire"), bool):
                continue
            valid_rows.append(item)

    if not valid_rows:
        return get_simulated_readings(), True
    return valid_rows[-50:], False


# Refresh this view every five seconds without restarting the whole Streamlit app.
@st.fragment(run_every="5s")
def render_live_data() -> None:
    readings, is_simulated = load_readings()
    latest = readings[-1]
    if is_simulated:
        st.info("Showing simulated readings because the local readings file is missing or empty.")
    else:
        st.caption(f"Loaded {len(readings)} stored readings from python_backend/data/readings.json")

    metric_columns = st.columns(5)
    metric_columns[0].metric("Soil moisture", f"{latest['soil']:.1f}%")
    metric_columns[1].metric("Temperature", f"{latest['temperature']:.1f} °C")
    metric_columns[2].metric("Humidity", f"{latest['humidity']:.1f}%")
    metric_columns[3].metric("Water level", f"{latest['waterLevel']:.1f}%")
    metric_columns[4].metric("Fire status", "Detected" if latest["fire"] else "Clear")

    alerts = []
    if latest["fire"]:
        alerts.append(("critical", "Fire detected"))
    if latest["waterLevel"] < 20:
        alerts.append(("warning", "Low water level"))
    if latest["soil"] < 30:
        alerts.append(("warning", "Soil too dry, irrigation required"))
    if latest["temperature"] > 40:
        alerts.append(("warning", "High temperature"))

    st.subheader("Active alerts")
    if alerts:
        for severity, message in alerts:
            if severity == "critical":
                st.error(message)
            else:
                st.warning(message)
    else:
        st.success("All monitored conditions are within range.")

    frame = pd.DataFrame(readings)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    frame = frame.dropna(subset=["timestamp"]).tail(50).set_index("timestamp")
    frame["fireStatus"] = frame["fire"].astype(int)

    st.subheader("Last 50 readings")
    st.caption("Soil moisture, humidity, and reservoir level")
    st.line_chart(frame[["soil", "humidity", "waterLevel"]], height=280)
    chart_columns = st.columns(2)
    with chart_columns[0]:
        st.caption("Temperature (°C)")
        st.line_chart(frame[["temperature"]], height=240)
    with chart_columns[1]:
        st.caption("Fire status (1 = detected)")
        st.line_chart(frame[["fireStatus"]], height=240)


st.title("Live Data")
render_live_data()
