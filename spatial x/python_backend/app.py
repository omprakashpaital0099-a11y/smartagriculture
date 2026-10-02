"""Local Flask API for Fieldwise sensor data and irrigation control."""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

# Sensor thresholds, storage limits, and operating ranges.
CONFIG: dict[str, Any] = {
    "default_port": 5000,
    "max_readings": 500,
    "max_alert_records": 50,
    "soil_moisture_threshold": 30,
    "soil_moisture_target": 40,
    "minimum_water_level": 20,
    "high_temperature_threshold": 40,
    "manual_override_seconds": 30,
    "allowed_ranges": {
        "soil": (0, 100),
        "temperature": (-40, 85),
        "humidity": (0, 100),
        "waterLevel": (0, 100),
    },
}

# Paths and in-memory state shared by the request handlers.
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
READINGS_FILE = DATA_DIR / "readings.json"
PUBLIC_DIR = BASE_DIR / "public"
STARTED_AT = time.monotonic()
DATA_LOCK = threading.RLock()
readings: list[dict[str, Any]] = []
alert_history: list[dict[str, Any]] = []
active_alerts: dict[str, dict[str, Any]] = {}
pump: dict[str, Any] = {
    "state": "OFF",
    "mode": "AUTO",
    "updatedAt": datetime.now(timezone.utc).isoformat(),
    "manualOverrideUntil": None,
}

app = Flask(__name__, static_folder=str(PUBLIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
CORS(app)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Log each request with method, path, status, and elapsed time.
@app.before_request
def begin_request() -> None:
    request_started_at = time.perf_counter()
    request.environ["fieldwise.request_started_at"] = request_started_at


@app.after_request
def log_request(response: Response) -> Response:
    started_at = request.environ.get("fieldwise.request_started_at", time.perf_counter())
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    app.logger.info(
        "%s %s %s %.1fms",
        request.method,
        request.full_path.rstrip("?"),
        response.status_code,
        elapsed_ms,
    )
    return response

# Validate readings without coercing strings, booleans, or out-of-range values.
def validate_reading(body: Any) -> str | None:
    if not isinstance(body, dict):
        return "Request body must be a JSON object."

    for field, (minimum, maximum) in CONFIG["allowed_ranges"].items():
        value = body.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return f"Field '{field}' must be a finite number."
        if value < minimum or value > maximum:
            return f"Field '{field}' must be between {minimum} and {maximum}."

    if not isinstance(body.get("fire"), bool):
        return "Field 'fire' must be a boolean."
    return None


# Serialize persisted snapshots and cap the on-disk history at 500 readings.
def persist_readings_locked() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary_file = READINGS_FILE.with_suffix(".json.tmp")
    snapshot = json.dumps(readings[-CONFIG["max_readings"] :], indent=2)
    with temporary_file.open("w", encoding="utf-8", newline="\n") as output:
        output.write(snapshot + "\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary_file, READINGS_FILE)


# Open and create the JSON store before the app begins serving requests.
def initialize_storage() -> None:
    global readings
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not READINGS_FILE.exists():
        with DATA_LOCK:
            readings = []
            persist_readings_locked()
        return

    try:
        stored_data = json.loads(READINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read {READINGS_FILE}: {error}") from error
    if not isinstance(stored_data, list):
        raise RuntimeError(f"{READINGS_FILE} must contain a JSON array.")

    valid_readings = []
    for item in stored_data:
        if validate_reading(item) is None and isinstance(item.get("timestamp"), str):
            valid_readings.append({
                "soil": item["soil"],
                "temperature": item["temperature"],
                "humidity": item["humidity"],
                "waterLevel": item["waterLevel"],
                "fire": item["fire"],
                "timestamp": item["timestamp"],
            })

    with DATA_LOCK:
        readings = valid_readings[-CONFIG["max_readings"] :]
        alert_history.clear()
        active_alerts.clear()
        for reading in readings:
            update_alerts_locked(reading)
        if readings:
            apply_automatic_pump_locked(readings[-1])

# Add an alert only when its condition becomes active; mark it resolved afterward.
def update_alerts_locked(reading: dict[str, Any]) -> None:
    global alert_history
    timestamp = reading["timestamp"]
    conditions = (
        ("fire", "Fire detected", "critical", reading["fire"]),
        (
            "lowWater",
            "Low water level",
            "warning",
            reading["waterLevel"] < CONFIG["minimum_water_level"],
        ),
        (
            "soilDry",
            "Soil too dry, irrigation required",
            "warning",
            reading["soil"] < CONFIG["soil_moisture_threshold"],
        ),
        (
            "highTemperature",
            "High temperature",
            "warning",
            reading["temperature"] > CONFIG["high_temperature_threshold"],
        ),
    )
    next_active: dict[str, dict[str, Any]] = {}

    for alert_type, message, severity, is_active in conditions:
        if not is_active:
            continue
        existing = active_alerts.get(alert_type)
        if existing is not None:
            next_active[alert_type] = existing
            continue
        record = {
            "id": f"{alert_type}-{timestamp}",
            "type": alert_type,
            "message": message,
            "severity": severity,
            "timestamp": timestamp,
            "active": True,
        }
        alert_history.insert(0, record)
        next_active[alert_type] = record

    for alert_type, record in active_alerts.items():
        if alert_type not in next_active:
            record["active"] = False
            record["resolvedAt"] = timestamp

    active_alerts.clear()
    active_alerts.update(next_active)
    alert_history = alert_history[: CONFIG["max_alert_records"]]

# Apply hysteresis and honor an unexpired manual pump override.
def apply_automatic_pump_locked(reading: dict[str, Any], now: datetime | None = None) -> None:
    global pump
    now = now or datetime.now(timezone.utc)
    override_until = pump.get("manualOverrideUntil")
    if override_until:
        try:
            if datetime.fromisoformat(override_until) > now:
                return
        except ValueError:
            pass

    next_state = pump["state"]
    if reading["waterLevel"] <= CONFIG["minimum_water_level"]:
        next_state = "OFF"
    elif reading["soil"] < CONFIG["soil_moisture_threshold"]:
        next_state = "ON"
    elif reading["soil"] >= CONFIG["soil_moisture_target"]:
        next_state = "OFF"

    if next_state != pump["state"] or pump["mode"] != "AUTO" or override_until:
        pump = {
            "state": next_state,
            "mode": "AUTO",
            "updatedAt": now.isoformat(),
            "manualOverrideUntil": None,
        }


def current_pump_state_locked() -> dict[str, Any]:
    if readings:
        apply_automatic_pump_locked(readings[-1])
    result = copy.deepcopy(pump)
    result["manualOverride"] = result["mode"] == "MANUAL"
    return result

# Serve the existing connected dashboard from the project root URL.
@app.get("/")
def homepage() -> Response:
    return send_from_directory(PUBLIC_DIR, "sahil.html")

# Accept an ESP32/Arduino sample, update alerts and irrigation, and persist it.
@app.post("/api/data")
def post_data() -> Response | tuple[Response, int]:
    global readings
    body = request.get_json(silent=False)
    validation_error = validate_reading(body)
    if validation_error:
        return jsonify({"error": validation_error}), 400

    reading = {
        "soil": body["soil"],
        "temperature": body["temperature"],
        "humidity": body["humidity"],
        "waterLevel": body["waterLevel"],
        "fire": body["fire"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with DATA_LOCK:
        readings.append(reading)
        readings = readings[-CONFIG["max_readings"] :]
        update_alerts_locked(reading)
        apply_automatic_pump_locked(reading)
        persist_readings_locked()
        response = {
            "message": "Reading stored.",
            "reading": copy.deepcopy(reading),
            "activeAlerts": copy.deepcopy(list(active_alerts.values())),
            "pump": current_pump_state_locked(),
        }
    return jsonify(response), 201

# Return the latest reading, or a clear 404 until the first sensor post.
@app.get("/api/sensors")
def get_sensors() -> Response | tuple[Response, int]:
    with DATA_LOCK:
        if not readings:
            return jsonify({"error": "No sensor readings are available yet."}), 404
        return jsonify(copy.deepcopy(readings[-1]))

# Return up to 500 chronological readings for the frontend charts.
@app.get("/api/history")
def get_history() -> Response | tuple[Response, int]:
    raw_limit = request.args.get("limit", "20")
    try:
        limit = int(raw_limit)
    except ValueError:
        limit = 0
    if str(limit) != raw_limit or not 1 <= limit <= CONFIG["max_readings"]:
        return jsonify({"error": f"limit must be an integer from 1 to {CONFIG['max_readings']}."}), 400
    with DATA_LOCK:
        history = copy.deepcopy(readings[-limit:])
    return jsonify({"count": len(history), "readings": history})

# Expose active alert conditions and the latest 50 alert lifecycle records.
@app.get("/api/alerts")
def get_alerts() -> Response:
    with DATA_LOCK:
        response = {
            "active": copy.deepcopy(list(active_alerts.values())),
            "records": copy.deepcopy(alert_history[: CONFIG["max_alert_records"]]),
        }
    return jsonify(response)

# Read pump state or apply a 30-second manual command, matching the Node API.
@app.get("/api/pump")
def get_pump() -> Response:
    with DATA_LOCK:
        return jsonify(current_pump_state_locked())


@app.post("/api/pump")
def post_pump() -> Response | tuple[Response, int]:
    global pump
    body = request.get_json(silent=False)
    requested_state = body.get("state") if isinstance(body, dict) else None
    if requested_state not in ("ON", "OFF"):
        return jsonify({"error": "state must be either 'ON' or 'OFF'."}), 400
    now = datetime.now(timezone.utc)
    with DATA_LOCK:
        pump = {
            "state": requested_state,
            "mode": "MANUAL",
            "updatedAt": now.isoformat(),
            "manualOverrideUntil": (
                now + timedelta(seconds=CONFIG["manual_override_seconds"])
            ).isoformat(),
        }
        return jsonify(current_pump_state_locked())

# Report basic process state and aggregate statistics over stored readings.
@app.get("/api/health")
def get_health() -> Response:
    with DATA_LOCK:
        latest_timestamp = readings[-1]["timestamp"] if readings else None
        reading_count = len(readings)
    return jsonify({
        "status": "ok",
        "uptimeSeconds": int(time.monotonic() - STARTED_AT),
        "readingCount": reading_count,
        "latestReadingAt": latest_timestamp,
    })


@app.get("/api/stats")
def get_stats() -> Response:
    sensor_fields = ("soil", "temperature", "humidity", "waterLevel", "fire")
    with DATA_LOCK:
        snapshot = copy.deepcopy(readings)
    statistics: dict[str, dict[str, float | int | None]] = {}
    for field in sensor_fields:
        values = [int(item[field]) if isinstance(item[field], bool) else item[field] for item in snapshot]
        statistics[field] = {
            "average": round(sum(values) / len(values), 2) if values else None,
            "minimum": min(values) if values else None,
            "maximum": max(values) if values else None,
        }
    return jsonify({"count": len(snapshot), "sensors": statistics})

# Keep HTTP and unexpected errors in a consistent JSON response format.
@app.errorhandler(HTTPException)
def handle_http_error(error: HTTPException) -> tuple[Response, int]:
    return jsonify({"error": error.description}), error.code


@app.errorhandler(Exception)
def handle_unexpected_error(error: Exception) -> tuple[Response, int]:
    app.logger.exception("Unhandled request error", exc_info=error)
    return jsonify({"error": "Internal server error."}), 500

# Load persisted readings once so recent values survive process restarts.
initialize_storage()

# Start the development server on PORT, falling back to port 5000.
if __name__ == "__main__":
    port_value = os.environ.get("PORT", str(CONFIG["default_port"]))
    try:
        port = int(port_value)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise SystemExit("PORT must be an integer from 1 to 65535.")
    app.run(host="0.0.0.0", port=port, threaded=True)
