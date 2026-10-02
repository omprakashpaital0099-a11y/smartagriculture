# Fieldwise local backend

A local Node.js + Express API for sensor ingestion, file-backed readings, alerts, and pump control. It does not use an IoT platform or authentication.

## Install and run

Use Node.js 18 or newer, then from this folder:

```sh
npm install
npm start
```

Open <http://localhost:3000>. Use `npm run dev` to run the server with nodemon. Set `PORT` to choose a different port, for example `PORT=3100 npm start` (PowerShell: `$env:PORT=3100; npm start`). Readings are kept in memory up to 500 entries and persisted to `data/readings.json`.

To test without hardware, keep the server running and open a second terminal in this folder:

```sh
node simulate.js
```

The simulator posts random sample readings immediately, then every three seconds.

## API examples

```sh
# Health
curl http://localhost:3000/api/health

# Submit a sensor reading
curl -X POST http://localhost:3000/api/data -H "Content-Type: application/json" -d "{\"soil\":45,\"temperature\":28,\"humidity\":60,\"waterLevel\":70,\"fire\":false}"

# Latest reading
curl http://localhost:3000/api/sensors

# Last 20 readings (choose 1 through 500)
curl "http://localhost:3000/api/history?limit=20"

# Active alerts and up to 50 recent alert records
curl http://localhost:3000/api/alerts

# Read pump status
curl http://localhost:3000/api/pump

# Manually turn the pump on (manual command takes priority for 30 seconds)
curl -X POST http://localhost:3000/api/pump -H "Content-Type: application/json" -d "{\"state\":\"ON\"}"

# Manually turn the pump off
curl -X POST http://localhost:3000/api/pump -H "Content-Type: application/json" -d "{\"state\":\"OFF\"}"
```

`POST /api/data` requires numeric `soil` (0-100), `temperature` (-40-85 C), `humidity` (0-100), `waterLevel` (0-100), and boolean `fire`. The server appends a timestamp. Alert thresholds and irrigation control values are at the top of `server.js`. Alerts are recorded once per active period and marked resolved when readings return to normal.

Automatic irrigation turns on below 30% soil moisture when water is above 20%, and turns off at 40% soil moisture or when water is at/below 20%. A manual pump request overrides automatic control for 30 seconds, after which automatic control resumes.

## Frontend API connection

The served `public/sahil.html` is a copy of the existing dashboard with an API adapter. It defines `API_URL = "/api/sensors"` and `HISTORY_URL = "/api/history?limit=20"`, loads those readings into the existing cards and charts, and polls `/api/pump`. If the API cannot be reached or has no reading yet, `syncDashboard()` catches the request failure and calls the existing `simulateReadings()` demo fallback. The original root-level `sahil.html` remains unchanged; the connected copy is `public/sahil.html`.

The exact endpoint constants already used in `public/sahil.html` are:

```js
const API_URL = "/api/sensors";
const HISTORY_URL = "/api/history?limit=20";
```

Its `syncDashboard()` requests both URLs, copies `soil`, `temperature`, `humidity`, `waterLevel`, and `fire` into the dashboard state, then calls `loadChartHistory(history.readings)`, `updateAlerts()`, and `render()`. On any failed request it sets `state.apiOnline = false` and calls `simulateReadings()`. The page starts that poll immediately and repeats it using `CONFIG.refreshIntervalMs`.

## ESP32 HTTP POST example

The ESP32 must use the computer's LAN IP address (not `localhost`), and both devices must be on the same network. Replace the Wi-Fi credentials and server IP below. This sample uses fixed sensor values as placeholders; replace them with actual sensor readings.

```cpp
#include <WiFi.h>
#include <HTTPClient.h>

const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* SERVER_URL = "http://192.168.1.20:3000/api/data";

void setup() {
  Serial.begin(115200);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWi-Fi connected");
}

void loop() {
  if (WiFi.status() == WL_CONNECTED) {
    float soil = 45;
    float temperature = 28;
    float humidity = 60;
    float waterLevel = 70;
    bool fire = false;

    String body = "{\"soil\":" + String(soil, 1) +
      ",\"temperature\":" + String(temperature, 1) +
      ",\"humidity\":" + String(humidity, 1) +
      ",\"waterLevel\":" + String(waterLevel, 1) +
      ",\"fire\":" + String(fire ? "true" : "false") + "}";

    WiFiClient client;
    HTTPClient http;
    if (http.begin(client, SERVER_URL)) {
      http.addHeader("Content-Type", "application/json");
      int status = http.POST(body);
      Serial.printf("POST status: %d\n", status);
      if (status > 0) Serial.println(http.getString());
      http.end();
    }
  }
  delay(3000);
}
```
