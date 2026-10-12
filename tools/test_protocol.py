#!/usr/bin/env python3
"""Offline self-tests for protocol.py: crypto round trip, framing, discovery parsing, value maps."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "godaikin_local"))

import protocol as p  # noqa: E402

KEY = "00112233445566778899aabbccddeeff"


def test_crypto_roundtrip() -> None:
    msg = '{"action":"getStatus","version":-1,"thingName":"DK_TEST"}'
    ct = p.encrypt_payload(msg, KEY)
    assert all(c in "0123456789abcdef" for c in ct) and len(ct) % 32 == 0
    assert p.decrypt_payload(ct, KEY) == msg
    # key == iv: first block must change if the key changes
    assert p.encrypt_payload(msg, "ff" * 16) != ct


def test_known_vector() -> None:
    # Independent check with a hand-computed AES-128-CBC vector (key == iv, PKCS7)
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = bytes.fromhex(KEY)
    enc = Cipher(algorithms.AES(key), modes.CBC(key)).encryptor()
    ref = enc.update(b"A" * 15 + b"\x01") + enc.finalize()
    assert p.encrypt_payload("A" * 15, KEY) == ref.hex()


def test_frames() -> None:
    frame = p.build_get_status("DK_TEST", KEY)
    assert frame.startswith(b'<{"payload":"') and frame.endswith(b'"}>')
    outer = p.unframe(frame)
    inner = json.loads(p.decrypt_payload(outer["payload"], KEY))
    assert inner == {"action": "getStatus", "version": -1, "thingName": "DK_TEST"}

    frame = p.build_set_status("DK_TEST", KEY, {"Set_OnOff": 1, "Set_Mode": 1, "Set_Temp": 24})
    inner = json.loads(p.decrypt_payload(p.unframe(frame)["payload"], KEY))
    assert inner["action"] == "setStatus" and inner["state"]["Set_Temp"] == 24 and inner["thingName"] == "DK_TEST"

    assert p.unframe(b'<{"ack": 1}>') == {"ack": 1}
    assert p.unframe(b'  <{"ack":1}>\r\n') == {"ack": 1}
    # Live observation: two frames in one TCP read ("Extra data ... char 750"); first one wins.
    two = b'<{\n\t"payload":\t"aabb"\n}>\n<{"ack":2}>'
    assert p.unframe(two) == {"payload": "aabb"}
    assert p.split_frames(two) == ['{"payload":"aabb"}', '{"ack":2}']
    assert p.has_complete_frame(b'<{"payload":"aa') is False
    assert p.has_complete_frame(b'<{"payload":"aa"}>') is True


def test_reply_parsing() -> None:
    state = {"Set_OnOff": 1, "Set_Mode": 1, "Set_Temp": "24", "Stat_IDRoomTemp": "27"}
    for doc in (state, {"state": {"reported": state}}, {"state": state}):
        ct = p.encrypt_payload(json.dumps(doc), KEY)
        parsed = p.parse_encrypted_reply(f'<{{"payload":"{ct}"}}>'.encode(), KEY)
        assert p.extract_state(parsed) == state, doc
    try:
        p.parse_encrypted_reply(f'<{{"payload":"{p.encrypt_payload("x", KEY)}"}}>'.encode(), "ff" * 16)
    except p.DaikinKeyError:
        pass
    else:
        raise AssertionError("wrong key must raise DaikinKeyError")


def test_discovery_parse() -> None:
    reply = b"{'LAN_DEV_DETECT': {'thingName': 'DK_abc123', 'version': '1_13_16'}}\n"
    u = p._parse_discovery_reply(reply, "192.168.1.50")
    assert u and u.thing_name == "DK_abc123" and u.host == "192.168.1.50"
    reply = b'{"LAN_DEV_DETECT":{"thingName":"DK_x"}}>'
    assert p._parse_discovery_reply(reply, "10.0.0.2").thing_name == "DK_x"
    assert p._parse_discovery_reply(b"garbage", "10.0.0.3") is None


def test_value_maps() -> None:
    assert p.MODE_TO_VALUE == {"cool": 1, "fan": 2, "dry": 4, "heat": 8, "auto": 10}
    assert p.fan_payload("auto") == {"Set_Fan": 128}
    assert p.fan_payload("level2") == {"Set_Fan": 2, "Set_FanExtend": 1}
    assert p.fan_payload("level5") == {"Set_Fan": 8, "Set_FanExtend": 0}
    assert p.fan_level_from_state({"Set_Fan": 4, "Set_FanExtend": 0}) == "level3"
    assert p.fan_level_from_state({"Set_Fan": "128"}) == "auto"
    assert p.fan_level_from_state({"Set_Fan": 8, "Set_FanExtend": 1}) == "level4"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} tests passed")
