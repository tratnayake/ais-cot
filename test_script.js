const WebSocket = require("ws");

const ws = new WebSocket("wss://stream.aisstream.io/v0/stream");

ws.on("open", () => {
  ws.send(
    JSON.stringify({
      APIKey: process.env.AISSTREAM_API_KEY,
      BoundingBoxes: [[[-90, -180], [90, 180]]],
    })
  );
  console.log("Subscribed with worldwide bbox, waiting for messages...");
});

let count = 0;
ws.on("message", (data) => {
  const msg = JSON.parse(data);
  count++;
  console.log(`[${count}] type=${msg.MessageType} mmsi=${msg.MetaData?.MMSI}`);
  if (count >= 5) {
    console.log("Got 5 messages — aisstream is working.");
    ws.close();
  }
});

ws.on("error", (err) => {
  console.error("WebSocket error:", err.message);
});

setTimeout(() => {
  console.log(`Timeout — only received ${count} messages in 15s.`);
  ws.close();
}, 15000);
