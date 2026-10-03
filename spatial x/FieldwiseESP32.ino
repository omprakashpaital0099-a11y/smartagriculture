#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <DHT.h>
#include <math.h>

// Enter these credentials locally before uploading. Do not commit real keys.
const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* THINGSPEAK_WRITE_API_KEY = "YOUR_THINGSPEAK_WRITE_API_KEY";
const char* THINGSPEAK_CHANNEL_ID = "3517806";

// Change to DHT11 if that is the sensor installed.
constexpr uint8_t DHT_SENSOR_TYPE = DHT22;

constexpr uint8_t DHT_PIN = 4;
constexpr uint8_t SOIL_PIN = 34;
constexpr uint8_t WATER_PIN = 35;
constexpr uint8_t FLAME_PIN = 32;

constexpr uint32_t SEND_INTERVAL_MS = 20000;
constexpr uint8_t ANALOG_SAMPLES = 10;
constexpr uint8_t DHT_MAX_ATTEMPTS = 4;
constexpr uint32_t DHT_RETRY_DELAY_MS = 2100;
constexpr uint32_t WIFI_STATUS_INTERVAL_MS = 5000;

static const char THINGSPEAK_ROOT_CA[] PROGMEM = R"EOF(
-----BEGIN CERTIFICATE-----
MIIDjjCCAnagAwIBAgIQAzrx5qcRqaC7KGSxHQn65TANBgkqhkiG9w0BAQsFADBh
MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3
d3cuZGlnaWNlcnQuY29tMSAwHgYDVQQDExdEaWdpQ2VydCBHbG9iYWwgUm9vdCBH
MjAeFw0xMzA4MDExMjAwMDBaFw0zODAxMTUxMjAwMDBaMGExCzAJBgNVBAYTAlVT
MRUwEwYDVQQKEwxEaWdpQ2VydCBJbmMxGTAXBgNVBAsTEHd3dy5kaWdpY2VydC5j
b20xIDAeBgNVBAMTF0RpZ2lDZXJ0IEdsb2JhbCBSb290IEcyMIIBIjANBgkqhkiG
9w0BAQEFAAOCAQ8AMIIBCgKCAQEAuzfNNNx7a8myaJCtSnX/RrohCgiN9RlUyfuI
2/Ou8jqJkTx65qsGGmvPrC3oXgkkRLpimn7Wo6h+4FR1IAWsULecYxpsMNzaHxmx
1x7e/dfgy5SDN67sH0NO3Xss0r0upS/kqbitOtSZpLYl6ZtrAGCSYP9PIUkY92eQ
q2EGnI/yuum06ZIya7XzV+hdG82MHauVBJVJ8zUtluNJbd134/tJS7SsVQepj5Wz
tCO7TG1F8PapspUwtP1MVYwnSlcUfIKdzXOS0xZKBgyMUNGPHgm+F6HmIcr9g+UQ
vIOlCsRnKPZzFBQ9RnbDhxSJITRNrw9FDKZJobq7nMWxM4MphQIDAQABo0IwQDAP
BgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIBhjAdBgNVHQ4EFgQUTiJUIBiV
5uNu5g/6+rkS7QYXjzkwDQYJKoZIhvcNAQELBQADggEBAGBnKJRvDkhj6zHd6mcY
1Yl9PMWLSn/pvtsrF9+wX3N3KjITOYFnQoQj8kVnNeyIv/iPsGEMNKSuIEyExtv4
NeF22d+mQrvHRAiGfzZ0JFrabA0UWTW98kndth/Jsw1HKj2ZL7tcu7XUIOGZX1NG
Fdtom/DzMNU+MeKNhJ7jitralj41E6Vf8PlwUHBHQRFXGU7Aj64GxJUTFy8bJZ91
8rGOmaFvE7FBcf6IKshPECBV1/MUReXgRPTqh5Uykw7+U0b6LJ3/iyK5S9kJRaTe
pLiaWN0bfVKfjllDiIGknibVb63dDcY3fe0Dkhvld1927jyNxF1WW6LZZm6zNTfl
MrY=
-----END CERTIFICATE-----
)EOF";

DHT dht(DHT_PIN, DHT_SENSOR_TYPE);
uint32_t lastCycleMs = 0;
uint32_t lastWifiStatusMs = 0;
uint32_t lastReconnectMs = 0;

uint16_t readAnalogAverage(uint8_t pin) {
  uint32_t total = 0;

  analogRead(pin);
  delay(5);

  for (uint8_t sample = 0; sample < ANALOG_SAMPLES; ++sample) {
    total += analogRead(pin);
    delay(5);
  }

  return static_cast<uint16_t>(total / ANALOG_SAMPLES);
}

bool readDht(float& humidity, float& temperature) {
  for (uint8_t attempt = 1; attempt <= DHT_MAX_ATTEMPTS; ++attempt) {
    humidity = dht.readHumidity();
    temperature = dht.readTemperature();

    if (isfinite(humidity) && isfinite(temperature)) {
      return true;
    }

    if (attempt < DHT_MAX_ATTEMPTS) {
      Serial.printf(
        "DHT read failed (attempt %u/%u); retrying...\n",
        attempt,
        DHT_MAX_ATTEMPTS
      );
      delay(DHT_RETRY_DELAY_MS);
    }
  }

  return false;
}

void sendToThingSpeak(
  uint16_t soilRaw,
  uint16_t waterRaw,
  uint16_t fireRaw,
  bool dhtOk,
  float humidity,
  float temperature
) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi disconnected; readings not sent this cycle.");
    return;
  }

  WiFiClientSecure secureClient;
  secureClient.setCACert(THINGSPEAK_ROOT_CA);

  String url = "https://api.thingspeak.com/update?api_key=";
  url += THINGSPEAK_WRITE_API_KEY;
  url += "&field1=";
  url += String(soilRaw);
  url += "&field2=";
  url += String(waterRaw);
  url += "&field3=";
  url += String(fireRaw);

  if (dhtOk) {
    url += "&field4=";
    url += String(humidity, 1);
    url += "&field5=";
    url += String(temperature, 1);
  } else {
    Serial.println("DHT read failed");
  }

  HTTPClient http;
  http.setConnectTimeout(10000);
  http.setTimeout(10000);

  if (!http.begin(secureClient, url)) {
    Serial.println("HTTP begin failed.");
    return;
  }

  Serial.println("Sending update to ThingSpeak...");
  const int httpCode = http.GET();
  Serial.printf("HTTP code: %d\n", httpCode);

  if (httpCode > 0) {
    const String response = http.getString();
    Serial.printf("ThingSpeak response: %s\n", response.c_str());
    if (response == "0") {
      Serial.println("ThingSpeak rejected the update (response 0).");
    }
  } else {
    Serial.printf(
      "HTTPS request failed: %s\n",
      http.errorToString(httpCode).c_str()
    );
  }

  http.end();
}

void setup() {
  Serial.begin(115200);
  delay(200);

  analogReadResolution(12);
  analogSetPinAttenuation(SOIL_PIN, ADC_11db);
  analogSetPinAttenuation(WATER_PIN, ADC_11db);
  analogSetPinAttenuation(FLAME_PIN, ADC_11db);

  dht.begin();

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  lastCycleMs = millis() - SEND_INTERVAL_MS;
  Serial.println("Fieldwise ESP32 sensor uploader started.");
  Serial.printf("ThingSpeak channel: %s\n", THINGSPEAK_CHANNEL_ID);
  Serial.printf("Connecting to Wi-Fi: %s\n", WIFI_SSID);
}

void loop() {
  const uint32_t now = millis();

  if (now - lastWifiStatusMs >= WIFI_STATUS_INTERVAL_MS) {
    lastWifiStatusMs = now;
    if (WiFi.status() == WL_CONNECTED) {
      Serial.printf(
        "Wi-Fi connected; IP address: %s\n",
        WiFi.localIP().toString().c_str()
      );
    } else {
      Serial.printf("Waiting for Wi-Fi; status: %d\n", WiFi.status());
      if (now - lastReconnectMs >= 10000) {
        lastReconnectMs = now;
        WiFi.reconnect();
      }
    }
  }

  if (now - lastCycleMs >= SEND_INTERVAL_MS) {
    lastCycleMs = now;

    const uint16_t soilRaw = readAnalogAverage(SOIL_PIN);
    const uint16_t waterRaw = readAnalogAverage(WATER_PIN);
    const uint16_t fireRaw = readAnalogAverage(FLAME_PIN);

    Serial.printf(
      "Raw readings — soil: %u, water: %u, fire: %u\n",
      soilRaw,
      waterRaw,
      fireRaw
    );

    float humidity = NAN;
    float temperature = NAN;
    const bool dhtOk = readDht(humidity, temperature);

    if (dhtOk) {
      Serial.printf(
        "DHT — humidity: %.1f%%, temperature: %.1f C\n",
        humidity,
        temperature
      );
    }

    sendToThingSpeak(
      soilRaw,
      waterRaw,
      fireRaw,
      dhtOk,
      humidity,
      temperature
    );
  }

  delay(50);
}
