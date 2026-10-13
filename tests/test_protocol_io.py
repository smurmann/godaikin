"""Exercise the TCP/UDP client code against an in-process fake unit."""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncGenerator, Callable
from typing import Any

import pytest

from custom_components.godaikin import protocol as p

# The HA test harness blocks sockets by default; these tests talk to an in-process fake unit.
pytestmark = pytest.mark.enable_socket

KEY = "00112233445566778899aabbccddeeff"
THING = "Daikin_aabbccddeeff"
STATE = {"Set_OnOff": 0, "Set_Fan": 4, "Set_Mode": 1, "Set_Temp": 21}


def _reply(doc: dict[str, Any]) -> bytes:
    return p.frame('{"payload":"' + p.encrypt_payload(json.dumps(doc), KEY) + '"}')


class FakeUnit:
    """Behaves like firmware V1.2.3: encrypted get/set, plaintext ack, silence on bad key."""

    def __init__(self) -> None:
        self.state = dict(STATE)
        self.mode: str = "normal"  # normal | silent | two_frames | garbage
        self.received: list[str] = []

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        raw = await reader.read(8192)
        try:
            outer = p.unframe(raw)
            inner = json.loads(p.decrypt_payload(outer["payload"], KEY)) if "payload" in outer else outer
        except Exception:  # the real unit just drops undecryptable frames
            inner = None
        self.received.append(json.dumps(inner))
        if inner is None or self.mode == "silent":
            pass
        elif self.mode == "garbage":
            writer.write(b"<{not json>")
        elif "ack" in inner:
            writer.write(b'<{"ack":2}>')
        elif inner.get("action") == "getStatus":
            doc = {"thingName": THING, "version": "V1.2.3", **self.state}
            writer.write(_reply(doc) + (b'\n<{"ack":2}>' if self.mode == "two_frames" else b""))
        elif inner.get("action") == "setStatus":
            self.state.update(inner["state"])
            writer.write(_reply({"thingName": THING, **inner["state"]}))
        await writer.drain()
        writer.close()


@pytest.fixture
async def fake_unit() -> AsyncGenerator[tuple[FakeUnit, int]]:
    unit = FakeUnit()
    server = await asyncio.start_server(unit.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield unit, port
    finally:
        server.close()
        await server.wait_closed()


def _client(port: int, key: str = KEY) -> p.DaikinLocalUnit:
    return p.DaikinLocalUnit("127.0.0.1", THING, key, port)


async def test_get_and_set_status(fake_unit: tuple[FakeUnit, int]) -> None:
    unit, port = fake_unit
    client = _client(port)
    status = await client.async_get_status()
    assert status["Set_Temp"] == 21 and status["thingName"] == THING
    reply = await client.async_set_status({"Set_Temp": 24, "Set_OnOff": 1})
    assert reply["Set_Temp"] == 24
    assert unit.state["Set_OnOff"] == 1
    assert json.loads(unit.received[-1])["thingName"] == THING


async def test_ping(fake_unit: tuple[FakeUnit, int]) -> None:
    _, port = fake_unit
    assert await _client(port).async_ping() is True
    assert await _client(1).async_ping() is False  # nothing listens on port 1


async def test_two_frames_in_one_read(fake_unit: tuple[FakeUnit, int]) -> None:
    unit, port = fake_unit
    unit.mode = "two_frames"
    assert (await _client(port).async_get_status())["Set_Mode"] == 1


async def test_wrong_key_is_silently_dropped(fake_unit: tuple[FakeUnit, int]) -> None:
    _, port = fake_unit
    with pytest.raises(p.DaikinLocalError, match="returned no data"):
        await _client(port, "ff" * 16)._exchange(p.build_get_status(THING, "ff" * 16), read_timeout=0.3)


async def test_garbage_reply(fake_unit: tuple[FakeUnit, int]) -> None:
    unit, port = fake_unit
    unit.mode = "garbage"
    with pytest.raises(ValueError):
        await _client(port).async_get_status()


def test_key_validation() -> None:
    with pytest.raises(p.DaikinKeyError, match="hex"):
        p.DaikinLocalUnit("h", THING, "not-hex-at-all")
    with pytest.raises(p.DaikinKeyError, match="16-byte"):
        p.DaikinLocalUnit("h", THING, "aabb")


class _Responder(asyncio.DatagramProtocol):
    def __init__(self, make_reply: Callable[[], bytes]) -> None:
        self._make = make_reply
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        assert data == p.DISCOVERY_MESSAGE.encode()
        assert self.transport is not None
        self.transport.sendto(self._make(), addr)


async def test_udp_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.get_running_loop()
    reply = f"{{'LAN_DEV_DETECT':{{'ACK':'OK','product_name':'AC','thingName':'{THING}'}}}}\n".encode()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _Responder(lambda: reply), local_addr=("127.0.0.1", 0)
    )
    target_port = transport.get_extra_info("sockname")[1]
    # pick a free local port for the listener instead of 61245
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        local_port = s.getsockname()[1]
    monkeypatch.setattr(p, "DISCOVERY_TARGET_PORT", target_port)
    try:
        units = await p.async_discover(
            broadcast="127.0.0.1", listen_for=0.3, local_port=local_port, targets=["127.0.0.1"]
        )
    finally:
        transport.close()
    assert len(units) == 1
    assert units[0].thing_name == THING and units[0].host == "127.0.0.1"
    assert units[0].raw["product_name"] == "AC"


async def test_udp_discovery_ignores_junk(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _Responder(lambda: b"junk"), local_addr=("127.0.0.1", 0)
    )
    monkeypatch.setattr(p, "DISCOVERY_TARGET_PORT", transport.get_extra_info("sockname")[1])
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        local_port = s.getsockname()[1]
    try:
        assert await p.async_discover(broadcast="127.0.0.1", listen_for=0.3, local_port=local_port) == []
    finally:
        transport.close()


def test_fan_helpers_edge_cases() -> None:
    assert p.fan_level_from_state({}, p.FAN_TABLE_3) is None
    assert p.to_int("x") is None and p.to_float(None) is None
    assert p.extract_state({"state": {"reported": {"Set_OnOff": 1}}}) == {"Set_OnOff": 1}
