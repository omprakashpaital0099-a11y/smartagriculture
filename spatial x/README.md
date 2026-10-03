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

The served `public/sahil.html` is embedded by the Streamlit app. The original root-level `sahil.html` remains unchanged; the hosted frontend is `public/sahil.html`.

The HTML front end in `public/sahil.html` is embedded by the repository-root
Streamlit app. It gets its current ThingSpeak snapshot through a single
`__FIELDWISE_STREAMLIT_DATA_JSON__` placeholder; it does not call the legacy
Fieldwise API routes.

## Streamlit Community Cloud and ThingSpeak

The repository-root `streamlit_app.py` reads up to 30 recent ThingSpeak entries every 15 seconds. Its `CONFIG` maps field1=raw soil, field2=raw 12-bit water, field3=raw fire, field4=humidity, and field5=temperature. Soil uses editable dry/wet raw calibration (provisional defaults 310/300); water is scaled by 4095 and flags a full-scale reading for sensor checking; fire uses an editable raw threshold and direction (provisional default: above 250). Temperature and humidity values that are missing or equal to 1 display as not reporting. The channel ID and optional read API key come from Streamlit app secrets and are never embedded in the HTML. On fetch failure, Streamlit reports the error and the dashboard shows simulated demo readings.

The Streamlit dashboard uses ThingSpeak for sensor readings only. Its pump relay control remains a demo control and does not operate the physical pump.

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
