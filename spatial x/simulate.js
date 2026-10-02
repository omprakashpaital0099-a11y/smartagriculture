// Post changing demo sensor values to the local Fieldwise API.
const API_URL = process.env.API_URL || "http://localhost:3000/api/data";

function randomReading() {
  return {
    soil: Math.round(22 + Math.random() * 55),
    temperature: Math.round((21 + Math.random() * 25) * 10) / 10,
    humidity: Math.round(40 + Math.random() * 50),
    waterLevel: Math.round(10 + Math.random() * 88),
    fire: Math.random() < 0.015
  };
}

async function postReading() {
  const reading = randomReading();
  try {
    const response = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(reading)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    console.log(`${new Date().toLocaleTimeString()} stored`, result.reading);
  } catch (error) {
    console.error(`${new Date().toLocaleTimeString()} post failed: ${error.message}`);
  }
}

postReading();
setInterval(postReading, 3000);
