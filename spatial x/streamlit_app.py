"""Streamlit entry point for the Fieldwise dashboard."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

PROJECT_DIR = Path(__file__).resolve().parent
FRONTEND_FILE = PROJECT_DIR / "public" / "sahil.html"

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

# Use a configured Flask host, or an empty base for the embedded demo fallback.
def get_api_base_url() -> str:
    try:
        return str(st.secrets["API_URL"]).strip().rstrip("/")
    except (KeyError, FileNotFoundError):
        return ""


# Rewrite the existing dashboard's API routes for cross-origin Flask hosting.
def build_dashboard_html(api_base_url: str) -> str:
    html = FRONTEND_FILE.read_text(encoding="utf-8")
    sensor_url = f"{api_base_url}/api/sensors" if api_base_url else ""
    history_url = f"{api_base_url}/api/history?limit=20" if api_base_url else ""
    pump_url = f"{api_base_url}/api/pump" if api_base_url else ""

    html = html.replace(
        'const API_URL = "/api/sensors";',
        f"const API_URL = {json.dumps(sensor_url)};",
    )
    html = html.replace(
        'const HISTORY_URL = "/api/history?limit=20";',
        f"const HISTORY_URL = {json.dumps(history_url)};",
    )
    html = html.replace('fetch("/api/pump"', f"fetch({json.dumps(pump_url)}")
    html = html.replace(
        "async function syncDashboard() {\n      try {",
        'async function syncDashboard() {\n      if (!API_URL) { state.apiOnline = false; simulateReadings(); return; }\n      try {',
    )
    return html


st.title("Fieldwise")
components.html(build_dashboard_html(get_api_base_url()), height=2200, scrolling=True)
