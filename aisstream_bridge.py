#!/usr/bin/env python3
"""
aisstream_bridge.py
Subscribes to aisstream.io WebSocket, converts AIS position reports to
Cursor on Target (CoT) XML, and forwards to a TAK Server via TCP.

Dependencies: websockets, asyncio (stdlib)
"""

import asyncio
import json
import logging
import os
import socket
import ssl
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("aisbridge")

# ── Config from environment ──────────────────────────────────────────────────
API_KEY        = os.environ["AISSTREAM_API_KEY"]
TAK_HOST       = os.environ.get("TAK_HOST", "127.0.0.1")
TAK_PORT       = int(os.environ.get("TAK_PORT", "8087"))
UPDATE_RATE    = int(os.environ.get("UPDATE_RATE", "10"))
COT_STALE      = int(os.environ.get("COT_STALE", "120"))
BBOX_LAT_MIN   = float(os.environ.get("BBOX_LAT_MIN", "47.5"))
BBOX_LAT_MAX   = float(os.environ.get("BBOX_LAT_MAX", "50.5"))
BBOX_LON_MIN   = float(os.environ.get("BBOX_LON_MIN", "-125.5"))
BBOX_LON_MAX   = float(os.environ.get("BBOX_LON_MAX", "-121.5"))
TYPE_FILTER    = [int(x) for x in os.environ.get(
    "TYPE_FILTER", "30,36,37,60,61,62,63,64,65,69,70,71,72,73,74,79,80,81,89"
).split(",")]

AISSTREAM_URL  = "wss://stream.aisstream.io/v0/stream"

# ── AIS ship type → CoT type mapping ─────────────────────────────────────────
def ais_type_to_cot(ship_type: int) -> str:
    if 60 <= ship_type <= 69:   return "a-u-S-X-M-F"   # Passenger
    if 70 <= ship_type <= 79:   return "a-u-S-X-M-C"   # Cargo
    if 80 <= ship_type <= 89:   return "a-u-S-X-M-T"   # Tanker
    if ship_type == 35:         return "a-f-S-X-M"      # Military
    if ship_type in (30,):      return "a-u-S-X-M"      # Fishing
    if ship_type in (36, 37):   return "a-u-S-X-L"      # Sailing/Pleasure
    return "a-u-S-X-M"                                  # Unknown surface

# ── Build CoT XML ─────────────────────────────────────────────────────────────
def build_cot(vessel: dict) -> bytes:
    now   = datetime.now(timezone.utc)
    stale = now + timedelta(seconds=COT_STALE)
    fmt   = "%Y-%m-%dT%H:%M:%S.%f"[:-3] + "Z"  # millisecond UTC

    mmsi      = str(vessel.get("mmsi", "000000000"))
    uid       = f"AIS.{mmsi}"
    lat       = vessel.get("lat", 0.0)
    lon       = vessel.get("lon", 0.0)
    cog       = vessel.get("cog", 9999.0)   # 9999 = unknown
    sog       = vessel.get("sog", 0.0)      # knots
    heading   = vessel.get("heading", 9999)
    name      = vessel.get("name", f"MMSI-{mmsi}").strip() or f"MMSI-{mmsi}"
    callsign  = vessel.get("callsign", "").strip()
    ship_type = vessel.get("ship_type", 0)
    cot_type  = ais_type_to_cot(ship_type)

    # CoT speed is in m/s; AIS SOG is knots
    speed_ms  = round(sog * 0.514444, 2)
    course    = cog if cog < 360.0 else 0.0
    true_head = heading if heading < 360 else course

    event = ET.Element("event")
    event.set("version",  "2.0")
    event.set("uid",      uid)
    event.set("type",     cot_type)
    event.set("time",     now.strftime(fmt))
    event.set("start",    now.strftime(fmt))
    event.set("stale",    stale.strftime(fmt))
    event.set("how",      "m-g")

    pt = ET.SubElement(event, "point")
    pt.set("lat",  str(round(lat, 6)))
    pt.set("lon",  str(round(lon, 6)))
    pt.set("hae",  "9999999.0")
    pt.set("ce",   "9999999.0")
    pt.set("le",   "9999999.0")

    detail = ET.SubElement(event, "detail")

    uid_el = ET.SubElement(detail, "uid")
    uid_el.set("Droid", name)

    track = ET.SubElement(detail, "track")
    track.set("course", str(round(course, 1)))
    track.set("speed",  str(speed_ms))

    contact = ET.SubElement(detail, "contact")
    contact.set("callsign", name)

    remarks = ET.SubElement(detail, "remarks")
    remarks.text = (
        f"MMSI: {mmsi} | "
        f"Callsign: {callsign or 'N/A'} | "
        f"SOG: {sog:.1f}kts | "
        f"COG: {course:.0f}° | "
        f"HDG: {true_head}° | "
        f"Type: {ship_type}"
    )

    return b'<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(event)

# ── TAK TCP sender ────────────────────────────────────────────────────────────
class TAKSender:
    def __init__(self, host: str, port: int):
        self.host   = host
        self.port   = port
        self._sock  = None

    def _connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect((self.host, self.port))
        self._sock = s
        log.info(f"Connected to TAK Server {self.host}:{self.port}")

    def send(self, data: bytes):
        for attempt in range(3):
            try:
                if not self._sock:
                    self._connect()
                self._sock.sendall(data + b"\n")
                return
            except (OSError, BrokenPipeError, ConnectionResetError) as e:
                log.warning(f"TAK send error (attempt {attempt+1}): {e}")
                self._sock = None
                if attempt == 2:
                    raise

    def close(self):
        if self._sock:
            self._sock.close()
            self._sock = None

# ── Vessel cache ──────────────────────────────────────────────────────────────
vessel_cache: dict = {}   # mmsi → vessel dict

# ── Main async loop ───────────────────────────────────────────────────────────
async def push_loop(tak: TAKSender):
    """Periodically flush cached vessels to TAK."""
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(UPDATE_RATE)
        snapshot = list(vessel_cache.values())
        sent = 0
        sent_vessels = []
        for vessel in snapshot:
            if vessel.get("lat") is None or vessel.get("lon") is None:
                continue
            try:
                cot = build_cot(vessel)
                await loop.run_in_executor(None, tak.send, cot)
                sent += 1
                mmsi = vessel.get("mmsi", "?")
                name = vessel.get("name", f"MMSI-{mmsi}").strip() or f"MMSI-{mmsi}"
                lat  = vessel.get("lat", 0.0)
                lon  = vessel.get("lon", 0.0)
                sent_vessels.append(f"{name} ({lat:.5f}, {lon:.5f})")
            except Exception as e:
                log.error(f"Failed to send CoT for {vessel.get('mmsi')}: {e}")
        if sent:
            vessel_list = " | ".join(sent_vessels)
            log.info(f"Pushed {sent} vessel(s) to TAK Server: {vessel_list}")
        else:
            log.info(f"Push cycle complete: 0 vessels sent (cache size: {len(vessel_cache)})")

async def ais_receive_loop():
    """Connect to aisstream.io and populate vessel cache."""
    sub_msg = {
        "APIKey": API_KEY,
        "BoundingBoxes": [[[BBOX_LAT_MIN, BBOX_LON_MIN], [BBOX_LAT_MAX, BBOX_LON_MAX]]],
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }

    backoff = 5
    while True:
        try:
            log.info(f"Connecting to aisstream.io...")
            async with websockets.connect(AISSTREAM_URL, ping_interval=20) as ws:
                await ws.send(json.dumps(sub_msg))
                log.info("Subscribed to aisstream.io feed")
                backoff = 5  # reset on successful connect
                msg_count = 0
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    msg_count += 1
                    mtype   = msg.get("MessageType", "")
                    meta    = msg.get("MetaData", {})

                    if msg_count == 1:
                        log.info(f"First message received: type={mtype} mmsi={meta.get('MMSI','?')}")
                    if msg_count % 10 == 0:
                        log.info(f"Received {msg_count} messages, {len(vessel_cache)} vessels in cache")
                    mmsi    = str(meta.get("MMSI", ""))
                    if not mmsi:
                        continue

                    if mmsi not in vessel_cache:
                        vessel_cache[mmsi] = {"mmsi": mmsi}

                    v = vessel_cache[mmsi]

                    if mtype == "PositionReport":
                        pr = msg.get("Message", {}).get("PositionReport", {})
                        ship_type = pr.get("ShipType", v.get("ship_type", 0))
                        # TYPE_FILTER check
                        if TYPE_FILTER and ship_type not in TYPE_FILTER:
                            # Allow if we haven't seen type yet (static not received)
                            if v.get("ship_type") and v["ship_type"] not in TYPE_FILTER:
                                continue
                        update = {
                            "lat":       meta.get("latitude",  pr.get("Latitude")),
                            "lon":       meta.get("longitude", pr.get("Longitude")),
                            "cog":       pr.get("Cog", 0.0),
                            "sog":       pr.get("Sog", 0.0),
                            "heading":   pr.get("TrueHeading", 511),
                            "ship_type": ship_type,
                        }
                        meta_name = (meta.get("ShipName") or "").strip()
                        if meta_name and not v.get("name"):
                            update["name"] = meta_name
                        v.update(update)

                    elif mtype == "ShipStaticData":
                        sd = msg.get("Message", {}).get("ShipStaticData", {})
                        ship_type = sd.get("Type", v.get("ship_type", 0))
                        if TYPE_FILTER and ship_type and ship_type not in TYPE_FILTER:
                            vessel_cache.pop(mmsi, None)
                            continue
                        v.update({
                            "name":      sd.get("Name", v.get("name", "")),
                            "callsign":  sd.get("CallSign", v.get("callsign", "")),
                            "ship_type": ship_type,
                        })

        except websockets.exceptions.ConnectionClosed as e:
            log.warning(f"aisstream.io disconnected: {e}. Reconnecting in {backoff}s...")
        except Exception as e:
            log.error(f"aisstream.io error: {e}. Reconnecting in {backoff}s...")

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)

async def main():
    log.info(f"aisstream → TAK bridge starting")
    log.info(f"TAK Server : {TAK_HOST}:{TAK_PORT}")
    log.info(f"Bounding box: [{BBOX_LAT_MIN},{BBOX_LON_MIN}] → [{BBOX_LAT_MAX},{BBOX_LON_MAX}]")
    log.info(f"Update rate : {UPDATE_RATE}s | CoT stale: {COT_STALE}s")

    tak = TAKSender(TAK_HOST, TAK_PORT)
    await asyncio.gather(
        ais_receive_loop(),
        push_loop(tak),
    )

if __name__ == "__main__":
    asyncio.run(main())