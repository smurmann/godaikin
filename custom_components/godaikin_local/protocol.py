"""Local LAN protocol for GO DAIKIN (my.com.daikin.app) Wi-Fi air conditioners.

Reconstructed from the decompiled Hermes bundle of GO DAIKIN 2.2.88 and verified live on
two units running adapter firmware V1.2.3 (docs/PROTOCOL.md has the evidence trail).

Transport summary
-----------------
Discovery   UDP ``{"LAN_DEV_DETECT":"LAN_DEV_DETECT"}`` to port 61244 (broadcast or unicast);
            replies arrive on local port 61245 as a Python-style dict literal plus one
            trailing newline: ``{'LAN_DEV_DETECT':{'ACK':'OK','product_name':'AC','thingName':...}}``.
Control     TCP port 7002.  Every message is framed ``<{...json...}>``.  Status and control
            documents are AES-128-CBC encrypted with key == IV == the unit key (32 hex chars),
            PKCS#7 padded, hex encoded, and wrapped as ``<{"payload":"<hex>"}>``.
Ping        Plain ``<{"ack": 1}>``; the unit answers ``<{"ack":2}>``.

The unit silently drops any frame it cannot decrypt.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import logging
import re
import socket
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_LOGGER = logging.getLogger(__name__)

# --- Constants observed in the app bundle --------------------------------------
DISCOVERY_MESSAGE = '{"LAN_DEV_DETECT":"LAN_DEV_DETECT"}'
DISCOVERY_REPLY_KEY = "LAN_DEV_DETECT"
DISCOVERY_TARGET_PORT = 61244
DISCOVERY_LOCAL_PORT = 61245
DISCOVERY_TIMEOUT = 5.0

CONTROL_PORT = 7002
CONTROL_TIMEOUT = 5.0
CONNECT_TIMEOUT = 3.0
PING_MESSAGE = '<{"ack": 1}>'

# Status/control field names (same vocabulary as the AWS IoT shadow used by the cloud path)
F_ONOFF = "Set_OnOff"
F_MODE = "Set_Mode"
F_TEMP = "Set_Temp"
F_FAN = "Set_Fan"
F_FAN_EXT = "Set_FanExtend"
F_SWING = "Set_Swing"
F_UDLVR = "Set_UDLvr"
F_SILENT = "Set_Silent"
F_TURBO = "Set_Turbo"
F_SLEEP = "Set_Sleep"
F_ION = "Set_Ion"
F_ROOM_TEMP = "Stat_IDRoomTemp"
F_OUTDOOR_TEMP = "Stat_ODAirTemp"
F_HUMIDITY = "Sta_IDRh"
F_ERROR = "Stat_ErrorCode"
F_POWER = "Stat_ODPowerConsumption"
F_VERSION = "version"
F_UPDATED = "updatedOn"
F_THING = "thingName"

# Set_Mode values (ModeKeyMap, bundle module 1783)
MODE_TO_VALUE = {"cool": 1, "fan": 2, "dry": 4, "heat": 8, "auto": 10}
VALUE_TO_MODE = {v: k for k, v in MODE_TO_VALUE.items()}

# Fan speed tables (bundle module 1765).  Values are (Set_Fan, Set_FanExtend);
# a None extend means the field is not sent.  Names are Home Assistant's fan vocabulary.
FAN_AUTO = "auto"
FAN_LOW = "low"
FAN_MEDIUM_LOW = "medium_low"
FAN_MEDIUM = "medium"
FAN_MEDIUM_HIGH = "medium_high"
FAN_HIGH = "high"

# 5-speed handset ("DAMA")
FAN_TABLE_5: dict[str, tuple[int, int | None]] = {
    FAN_AUTO: (128, None),
    FAN_LOW: (2, 0),
    FAN_MEDIUM_LOW: (2, 1),
    FAN_MEDIUM: (4, 0),
    FAN_MEDIUM_HIGH: (8, 1),
    FAN_HIGH: (8, 0),
}
# 3-speed handset ("DIL"), verified live: Set_Fan 2/4/8 with no Set_FanExtend.
FAN_TABLE_3: dict[str, tuple[int, int | None]] = {
    FAN_AUTO: (128, None),
    FAN_LOW: (2, None),
    FAN_MEDIUM: (4, None),
    FAN_HIGH: (8, None),
}

# Vertical louver: Set_Swing 1 + Set_UDLvr 15 = full swing; Set_Swing 0 + Set_UDLvr n = fixed n (1..5)
SWING_FULL = (1, 15)
SWING_OFF = (0, 0)
LOUVER_POSITIONS = (1, 2, 3, 4, 5)

_FRAME_RE = re.compile(r"<\s*(\{.*?\})\s*>", re.S)


class DaikinLocalError(Exception):
    """Base error."""


class DaikinKeyError(DaikinLocalError):
    """The unit rejected our key or we could not decrypt its reply."""


# --- Crypto ---------------------------------------------------------------------


def _key_bytes(unit_key: str) -> bytes:
    key = unit_key.strip()
    try:
        raw = bytes.fromhex(key)
    except ValueError as err:
        raise DaikinKeyError(
            "unit key must be hex (the app decodes it with Buffer.from(key, 'hex'))"
        ) from err
    if len(raw) != 16:
        raise DaikinKeyError(f"AES-128 needs a 16-byte key, got {len(raw)} bytes")
    return raw


def encrypt_payload(plaintext: str, unit_key: str) -> str:
    """AES-128-CBC, key == iv, PKCS7, hex output (bundle module 2149 defaults)."""
    key = _key_bytes(unit_key)
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(key)).encryptor()
    return (enc.update(data) + enc.finalize()).hex()


def decrypt_payload(hex_ciphertext: str, unit_key: str) -> str:
    key = _key_bytes(unit_key)
    try:
        data = bytes.fromhex(hex_ciphertext.strip())
        dec = Cipher(algorithms.AES(key), modes.CBC(key)).decryptor()
        padded = dec.update(data) + dec.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as err:
        raise DaikinKeyError("could not decrypt reply; the unit key is probably wrong or expired") from err


# --- Framing ----------------------------------------------------------------------


def frame(obj_json: str) -> bytes:
    return f"<{obj_json}>".encode()


def split_frames(raw: bytes) -> list[str]:
    """Return the JSON bodies of every complete <{...}> frame in the buffer."""
    text = raw.decode("utf-8", errors="replace")
    return [re.sub(r"\s", "", m.group(1)) for m in _FRAME_RE.finditer(text)]


def has_complete_frame(raw: bytes) -> bool:
    return bool(_FRAME_RE.search(raw.decode("utf-8", errors="replace")))


def unframe(raw: bytes) -> dict[str, Any]:
    """Parse the first complete frame.  Units occasionally send two frames in one read."""
    frames = split_frames(raw)
    if frames:
        return json.loads(frames[0])
    return json.loads(re.sub(r"\s|<|>", "", raw.decode("utf-8", errors="replace")))


def build_get_status(thing_name: str, unit_key: str) -> bytes:
    inner = f'{{"action":"getStatus","version":-1,"thingName":"{thing_name}"}}'
    return frame(f'{{"payload":"{encrypt_payload(inner, unit_key)}"}}')


def build_set_status(thing_name: str, unit_key: str, state: dict[str, Any]) -> bytes:
    inner = (
        '{"action":"setStatus","version":-1,"state":'
        + json.dumps(state, separators=(",", ":"))
        + f',"thingName":"{thing_name}"}}'
    )
    return frame(f'{{"payload":"{encrypt_payload(inner, unit_key)}"}}')


def parse_encrypted_reply(raw: bytes, unit_key: str) -> dict[str, Any]:
    outer = unframe(raw)
    if "payload" not in outer:
        return outer  # plaintext replies (e.g. ack) pass through
    plain = re.sub(r"\s", "", decrypt_payload(outer["payload"], unit_key))
    return json.loads(plain)


def extract_state(reply: dict[str, Any]) -> dict[str, Any]:
    """Locate the state inside a getStatus reply.

    Verified firmware (V1.2.3) returns a flat document; older/newer firmware might wrap it
    in an AWS-shadow-style envelope, so those shapes are tolerated too.
    """
    for path in (("state", "reported"), ("state",), ("reported",)):
        node: Any = reply
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict) and F_ONOFF in node:
            return node
    return reply


# --- Discovery -------------------------------------------------------------------


@dataclass(slots=True)
class DiscoveredUnit:
    thing_name: str
    host: str
    raw: dict[str, Any] = field(default_factory=dict)


def _parse_discovery_reply(data: bytes, source: str) -> DiscoveredUnit | None:
    text = data.decode("utf-8", errors="replace")
    for raw_candidate in (text[:-1], text):
        candidate = raw_candidate.strip()
        try:
            obj = json.loads(candidate.replace("'", '"'))
        except json.JSONDecodeError:
            try:
                obj = ast.literal_eval(candidate)
            except (ValueError, SyntaxError):
                continue
        if not isinstance(obj, dict):
            continue
        info = obj.get(DISCOVERY_REPLY_KEY, obj)
        if isinstance(info, dict) and F_THING in info:
            return DiscoveredUnit(thing_name=str(info[F_THING]), host=source, raw=info)
    _LOGGER.debug("Unparseable discovery reply from %s: %r", source, text)
    return None


async def async_discover(
    broadcast: str = "255.255.255.255",
    listen_for: float = DISCOVERY_TIMEOUT,
    local_port: int = DISCOVERY_LOCAL_PORT,
    targets: list[str] | None = None,
) -> list[DiscoveredUnit]:
    """Broadcast discovery; pass ``targets`` (unicast IPs) when units sit on another VLAN."""
    loop = asyncio.get_running_loop()
    found: dict[str, DiscoveredUnit] = {}

    class _Proto(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
            unit = _parse_discovery_reply(data, addr[0])
            if unit:
                found[unit.thing_name] = unit

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)
    sock.bind(("0.0.0.0", local_port))
    transport, _ = await loop.create_datagram_endpoint(_Proto, sock=sock)
    try:
        for dest in [broadcast, *(targets or [])]:
            transport.sendto(DISCOVERY_MESSAGE.encode(), (dest, DISCOVERY_TARGET_PORT))
        await asyncio.sleep(listen_for)
    finally:
        transport.close()
    return list(found.values())


# --- TCP client ----------------------------------------------------------------------


class DaikinLocalUnit:
    """One air conditioner reachable on the LAN.  One short-lived TCP connection per exchange."""

    def __init__(self, host: str, thing_name: str, unit_key: str, port: int = CONTROL_PORT) -> None:
        self.host = host
        self.port = port
        self.thing_name = thing_name
        self.unit_key = unit_key
        _key_bytes(unit_key)  # validate early

    async def _exchange(self, message: bytes, read_timeout: float = CONTROL_TIMEOUT) -> bytes:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), CONNECT_TIMEOUT
        )
        try:
            writer.write(message)
            await writer.drain()
            data = b""
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), read_timeout)
                except TimeoutError:
                    break
                if not chunk:
                    break
                data += chunk
                if has_complete_frame(data):
                    break
            if not data:
                raise DaikinLocalError(f"{self.host}:{self.port} returned no data (wrong key?)")
            return data
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def async_ping(self) -> bool:
        try:
            return bool(await self._exchange(PING_MESSAGE.encode(), read_timeout=1.0))
        except (OSError, TimeoutError, DaikinLocalError):
            return False

    async def async_get_status(self) -> dict[str, Any]:
        raw = await self._exchange(build_get_status(self.thing_name, self.unit_key))
        return extract_state(parse_encrypted_reply(raw, self.unit_key))

    async def async_set_status(self, state: dict[str, Any]) -> dict[str, Any]:
        raw = await self._exchange(build_set_status(self.thing_name, self.unit_key, state))
        return parse_encrypted_reply(raw, self.unit_key)


# --- Value helpers ---------------------------------------------------------------------


def is_five_speed(state: dict[str, Any]) -> bool:
    """3-speed handsets never report Set_FanExtend."""
    return F_FAN_EXT in state


def fan_table(state: dict[str, Any], force_five: bool = False) -> dict[str, tuple[int, int | None]]:
    return FAN_TABLE_5 if force_five or is_five_speed(state) else FAN_TABLE_3


def fan_payload(level: str, table: dict[str, tuple[int, int | None]]) -> dict[str, int]:
    fan, ext = table[level]
    out = {F_FAN: fan}
    if ext is not None:
        out[F_FAN_EXT] = ext
    return out


def fan_level_from_state(state: dict[str, Any], table: dict[str, tuple[int, int | None]]) -> str | None:
    fan = to_int(state.get(F_FAN))
    ext = to_int(state.get(F_FAN_EXT))
    for name, (f, e) in table.items():
        if fan == f and (e is None or ext is None or e == ext):
            return name
    return None


def to_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
