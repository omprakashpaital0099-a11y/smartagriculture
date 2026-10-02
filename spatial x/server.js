// Fieldwise Express API: local sensor ingestion, alert evaluation, and pump control.
const express = require("express");
const cors = require("cors");
const fs = require("node:fs/promises");
const path = require("node:path");

// Keep hardware thresholds and operating limits together for easy tuning.
const CONFIG = {
  defaultPort: 3000,
  maxReadings: 500,
  maxAlertRecords: 50,
  soilMoistureThreshold: 30,
  soilMoistureTarget: 40,
  minimumWaterLevel: 20,
  highTemperatureThreshold: 40,
  manualOverrideMs: 30_000,
  allowedRanges: {
    soil: [0, 100],
    temperature: [-40, 85],
    humidity: [0, 100],
    waterLevel: [0, 100]
  }
};

const ROOT_DIR = __dirname;
const DATA_DIR = path.join(ROOT_DIR, "data");
const READINGS_FILE = path.join(DATA_DIR, "readings.json");
const PUBLIC_DIR = path.join(ROOT_DIR, "public");
const app = express();

let readings = [];
let alertHistory = [];
let activeAlerts = new Map();
let persistenceQueue = Promise.resolve();
let pump = {
  state: "OFF",
  mode: "AUTO",
  updatedAt: new Date().toISOString(),
  manualOverrideUntil: null
};

// Log each request after its response so duration and status are available.
app.use((req, res, next) => {
  const startedAt = process.hrtime.bigint();
  res.on("finish", () => {
    const durationMs = Number(process.hrtime.bigint() - startedAt) / 1e6;
    console.log(`${new Date().toISOString()} ${req.method} ${req.originalUrl} ${res.statusCode} ${durationMs.toFixed(1)}ms`);
  });
  next();
});

app.use(cors());
app.use(express.json({ limit: "16kb" }));

// Serialize snapshots so concurrent sensor posts cannot overwrite newer data.
function persistReadings() {
  const snapshot = JSON.stringify(readings.slice(-CONFIG.maxReadings), null, 2);
  persistenceQueue = persistenceQueue
    .catch(() => {})
    .then(() => fs.writeFile(READINGS_FILE, `${snapshot}\n`, "utf8"));
  return persistenceQueue;
}

// Validate the complete sensor payload without coercing strings or booleans.
function validateReading(body) {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return "Request body must be a JSON object.";
  }

  for (const [field, [minimum, maximum]] of Object.entries(CONFIG.allowedRanges)) {
    const value = body[field];
    if (typeof value !== "number" || !Number.isFinite(value)) {
      return `Field '${field}' must be a finite number.`;
    }
    if (value < minimum || value > maximum) {
      return `Field '${field}' must be between ${minimum} and ${maximum}.`;
    }
  }

  if (typeof body.fire !== "boolean") {
    return "Field 'fire' must be a boolean.";
  }
  return null;
}

// Create one alert record when a condition becomes active; resolve it in place.
function updateAlerts(reading) {
  const timestamp = reading.timestamp;
  const conditions = [
    { type: "fire", message: "Fire detected", severity: "critical", active: reading.fire },
    { type: "lowWater", message: "Low water level", severity: "warning", active: reading.waterLevel < CONFIG.minimumWaterLevel },
    { type: "soilDry", message: "Soil too dry, irrigation required", severity: "warning", active: reading.soil < CONFIG.soilMoistureThreshold },
    { type: "highTemperature", message: "High temperature", severity: "warning", active: reading.temperature > CONFIG.highTemperatureThreshold }
  ];
  const nextActive = new Map();

  for (const condition of conditions) {
    if (!condition.active) continue;
    const existing = activeAlerts.get(condition.type);
    if (existing) {
      nextActive.set(condition.type, existing);
      continue;
    }

    const record = {
      id: `${condition.type}-${timestamp}`,
      type: condition.type,
      message: condition.message,
      severity: condition.severity,
      timestamp,
      active: true
    };
    alertHistory.unshift(record);
    nextActive.set(condition.type, record);
  }

  for (const [type, record] of activeAlerts) {
    if (!nextActive.has(type)) {
      record.active = false;
      record.resolvedAt = timestamp;
    }
  }

  activeAlerts = nextActive;
  alertHistory = alertHistory.slice(0, CONFIG.maxAlertRecords);
}

// Automatic irrigation uses separate start/stop thresholds to prevent relay chatter.
function applyAutomaticPump(reading, now = Date.now()) {
  if (pump.manualOverrideUntil && Date.parse(pump.manualOverrideUntil) > now) return;

  if (pump.mode === "MANUAL") {
    pump.mode = "AUTO";
    pump.manualOverrideUntil = null;
  }

  let nextState = pump.state;
  if (reading.waterLevel <= CONFIG.minimumWaterLevel) {
    nextState = "OFF";
  } else if (reading.soil < CONFIG.soilMoistureThreshold) {
    nextState = "ON";
  } else if (reading.soil >= CONFIG.soilMoistureTarget) {
    nextState = "OFF";
  }

  if (nextState !== pump.state || pump.mode !== "AUTO") {
    pump.state = nextState;
    pump.mode = "AUTO";
    pump.updatedAt = new Date(now).toISOString();
  }
}

function currentPumpState() {
  const latest = readings.at(-1);
  if (latest) applyAutomaticPump(latest);
  return {
    state: pump.state,
    mode: pump.mode,
    manualOverride: pump.mode === "MANUAL",
    manualOverrideUntil: pump.manualOverrideUntil,
    updatedAt: pump.updatedAt
  };
}

// Accept validated sensor readings and update persistence, alerts, and automation.
app.post("/api/data", async (req, res, next) => {
  try {
    const validationError = validateReading(req.body);
    if (validationError) {
      return res.status(400).json({ error: validationError });
    }

    const reading = {
      soil: req.body.soil,
      temperature: req.body.temperature,
      humidity: req.body.humidity,
      waterLevel: req.body.waterLevel,
      fire: req.body.fire,
      timestamp: new Date().toISOString()
    };

    readings.push(reading);
    readings = readings.slice(-CONFIG.maxReadings);
    updateAlerts(reading);
    applyAutomaticPump(reading);
    await persistReadings();

    return res.status(201).json({
      message: "Reading stored.",
      reading,
      activeAlerts: [...activeAlerts.values()],
      pump: currentPumpState()
    });
  } catch (error) {
    return next(error);
  }
});

// Return the latest sensor sample, or a useful not-found response before the first post.
app.get("/api/sensors", (req, res) => {
  const latest = readings.at(-1);
  if (!latest) return res.status(404).json({ error: "No sensor readings are available yet." });
  return res.json(latest);
});

// Return a bounded, chronological slice so chart clients can draw left-to-right.
app.get("/api/history", (req, res) => {
  const rawLimit = req.query.limit === undefined ? "20" : req.query.limit;
  const limit = Number(rawLimit);
  if (!Number.isInteger(limit) || limit < 1 || limit > CONFIG.maxReadings) {
    return res.status(400).json({ error: `limit must be an integer from 1 to ${CONFIG.maxReadings}.` });
  }

  const history = readings.slice(-limit);
  return res.json({ count: history.length, readings: history });
});

// Expose current conditions and the most recent alert lifecycle records.
app.get("/api/alerts", (req, res) => {
  return res.json({
    active: [...activeAlerts.values()],
    records: alertHistory.slice(0, CONFIG.maxAlertRecords)
  });
});

// Let an ESP32 read the relay state or request a time-bounded manual override.
app.get("/api/pump", (req, res) => res.json(currentPumpState()));
app.post("/api/pump", (req, res) => {
  const requestedState = req.body && req.body.state;
  if (requestedState !== "ON" && requestedState !== "OFF") {
    return res.status(400).json({ error: "state must be either 'ON' or 'OFF'." });
  }

  const now = Date.now();
  pump = {
    state: requestedState,
    mode: "MANUAL",
    updatedAt: new Date(now).toISOString(),
    manualOverrideUntil: new Date(now + CONFIG.manualOverrideMs).toISOString()
  };
  return res.json(currentPumpState());
});

// Basic process health information for local monitoring and deployment checks.
app.get("/api/health", (req, res) => {
  res.json({
    status: "ok",
    uptimeSeconds: Math.floor(process.uptime()),
    readingCount: readings.length,
    latestReadingAt: readings.at(-1)?.timestamp || null
  });
});

// Keep missing API routes JSON-shaped, then serve the frontend as static content.
app.use("/api", (req, res) => res.status(404).json({ error: "API endpoint not found." }));
app.use(express.static(PUBLIC_DIR));
app.get("/", (req, res) => res.sendFile(path.join(PUBLIC_DIR, "sahil.html")));
app.use((req, res) => res.status(404).json({ error: "Not found." }));

// Normalize JSON parse errors and unexpected failures into consistent responses.
app.use((error, req, res, next) => {
  if (res.headersSent) return next(error);
  const status = error.status || (error.type === "entity.parse.failed" ? 400 : 500);
  if (status >= 500) console.error(error);
  return res.status(status).json({
    error: status === 400 ? "Invalid JSON request body." : "Internal server error."
  });
});

// Load the file-backed reading history and create the storage file if necessary.
async function initializeStorage() {
  await fs.mkdir(DATA_DIR, { recursive: true });
  try {
    const contents = await fs.readFile(READINGS_FILE, "utf8");
    const storedReadings = JSON.parse(contents);
    if (!Array.isArray(storedReadings)) throw new Error("readings.json must contain a JSON array.");
    readings = storedReadings.slice(-CONFIG.maxReadings);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    readings = [];
    await fs.writeFile(READINGS_FILE, "[]\n", "utf8");
  }

  const latest = readings.at(-1);
  if (latest) {
    updateAlerts(latest);
    applyAutomaticPump(latest);
  }
}

async function startServer() {
  await initializeStorage();
  const port = Number(process.env.PORT) || CONFIG.defaultPort;
  return app.listen(port, () => console.log(`Fieldwise API listening at http://localhost:${port}`));
}

if (require.main === module) {
  startServer().catch((error) => {
    console.error("Could not start Fieldwise API:", error);
    process.exitCode = 1;
  });
}

module.exports = { app, CONFIG, initializeStorage };
