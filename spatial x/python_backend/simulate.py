"""Post randomized Fieldwise sensor readings to the local Flask API."""

import os
import random
import time

import requests

# Keep the simulator pointed at the local server unless overridden.
API_URL = os.environ.get("FIELDWISE_API_URL", "http://127.0.0.1:5000")
POST_URL = f"{API_URL.rstrip('/')}/api/data"
INTERVAL_SECONDS = 3

# Generate readings in the ranges accepted by app.py.
def make_reading() -> dict[str, int | float | bool]:
    return {
        "soil": random.randint(15, 75),
        "temperature": round(random.uniform(18, 45), 1),
        "humidity": random.randint(35, 90),
        "waterLevel": random.randint(10, 95),
        "fire": random.random() < 0.03,
    }

# Continue posting until stopped with Ctrl+C.
def main() -> None:
    print(f"Posting simulated sensor readings to {POST_URL} every {INTERVAL_SECONDS} seconds.")
    try:
        while True:
            reading = make_reading()
            try:
                response = requests.post(POST_URL, json=reading, timeout=5)
                response.raise_for_status()
                print(response.json())
            except requests.RequestException as error:
                print(f"Request failed: {error}")
            time.sleep(INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nSimulator stopped.")


if __name__ == "__main__":
    main()
