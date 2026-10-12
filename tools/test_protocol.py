#!/usr/bin/env python3
"""Offline tests: crypto round trip, framing, discovery parsing, value tables, cloud parsing.

Runs with plain `python3 tools/test_protocol.py` or under pytest.
"""

from __future__ import annotations

import json

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from _load import load

cloud = load("cloud")
p = load("protocol")

KEY = "00112233445566778899aabbccddeeff"


def test_crypto_roundtrip() -> None:
    msg = '{"action":"getStatus","version":-1,"thingName":"DK_TEST"}'
    ct = p.encrypt_payload(msg, KEY)
    assert all(c in "0123456789abcdef" for c in ct) and len(ct) % 32 == 0
    assert p.decrypt_payload(ct, KEY) == msg
    assert p.encrypt_payload(msg, "ff" * 16) != ct


def test_known_vector() -> None:
    key = bytes.fromhex(KEY)
    enc = Cipher(algorithms.AES(key), modes.CBC(key)).encryptor()
    ref = enc.update(b"A" * 15 + b"\x01") + enc.finalize()
    assert p.encrypt_payload("A" * 15, KEY) == ref.hex()


def test_frames() -> None:
    frame = p.build_get_status("DK_TEST", KEY)
    assert frame.startswith(b'<{"payload":"') and frame.endswith(b'"}>')
    inner = json.loads(p.decrypt_payload(p.unframe(frame)["payload"], KEY))
    assert inner == {"action": "getStatus", "version": -1, "thingName": "DK_TEST"}

    frame = p.build_set_status("DK_TEST", KEY, {"Set_OnOff": 1, "Set_Mode": 1, "Set_Temp": 24})
    inner = json.loads(p.decrypt_payload(p.unframe(frame)["payload"], KEY))
    assert inner["action"] == "setStatus"
    assert inner["state"]["Set_Temp"] == 24
    assert inner["thingName"] == "DK_TEST"

    assert p.unframe(b'<{"ack": 1}>') == {"ack": 1}
    assert p.unframe(b'  <{"ack":2}>\r\n') == {"ack": 2}
    # Live observation: two frames in one TCP read; the first one wins.
    two = b'<{\n\t"payload":\t"aabb"\n}>\n<{"ack":2}>'
    assert p.unframe(two) == {"payload": "aabb"}
    assert p.split_frames(two) == ['{"payload":"aabb"}', '{"ack":2}']
    assert p.has_complete_frame(b'<{"payload":"aa') is False
    assert p.has_complete_frame(b'<{"payload":"aa"}>') is True


def test_reply_parsing() -> None:
    state = {"Set_OnOff": 1, "Set_Mode": 1, "Set_Temp": 24, "Set_Fan": 4}
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
    # Exact live reply format
    reply = b"{'LAN_DEV_DETECT':{'ACK':'OK','product_name':'AC','thingName':'Daikin_aabbccddeeff'}}\n"
    unit = p._parse_discovery_reply(reply, "192.168.1.50")
    assert unit and unit.thing_name == "Daikin_aabbccddeeff" and unit.host == "192.168.1.50"
    assert unit.raw["product_name"] == "AC"
    assert (
        p._parse_discovery_reply(b'{"LAN_DEV_DETECT":{"thingName":"DK_x"}}>', "10.0.0.2").thing_name == "DK_x"
    )
    assert p._parse_discovery_reply(b"garbage", "10.0.0.3") is None


def test_value_maps() -> None:
    assert p.MODE_TO_VALUE == {"cool": 1, "fan": 2, "dry": 4, "heat": 8, "auto": 10}

    three = {"Set_OnOff": 0, "Set_Fan": 4}
    assert p.is_five_speed(three) is False
    assert p.fan_table(three) is p.FAN_TABLE_3
    assert p.fan_level_from_state(three, p.FAN_TABLE_3) == p.FAN_MEDIUM
    assert p.fan_level_from_state({"Set_Fan": 8}, p.FAN_TABLE_3) == p.FAN_HIGH
    assert p.fan_level_from_state({"Set_Fan": "128"}, p.FAN_TABLE_3) == p.FAN_AUTO
    assert p.fan_payload(p.FAN_LOW, p.FAN_TABLE_3) == {"Set_Fan": 2}

    five = {"Set_Fan": 8, "Set_FanExtend": 1}
    assert p.is_five_speed(five) is True
    assert p.fan_level_from_state(five, p.FAN_TABLE_5) == p.FAN_MEDIUM_HIGH
    assert p.fan_payload(p.FAN_MEDIUM_LOW, p.FAN_TABLE_5) == {"Set_Fan": 2, "Set_FanExtend": 1}
    assert p.fan_payload(p.FAN_AUTO, p.FAN_TABLE_5) == {"Set_Fan": 128}
    assert p.fan_table(three, force_five=True) is p.FAN_TABLE_5


def test_cloud_parsing() -> None:
    doc = {
        "body": json.dumps(
            {
                "units": [
                    {"thingName": "Daikin_a", "ACName": "Bedroom", "shadowState": {"key": "ab" * 16}},
                    {"thingName": "Daikin_b", "shadowState": {"state": {"reported": {"key": "cd" * 16}}}},
                    {"thingName": "Daikin_a"},  # duplicate without key must not shadow the keyed one
                ]
            }
        )
    }
    units = {u.thing_name: u for u in cloud.find_units(cloud.unwrap(doc))}
    assert units["Daikin_a"].key == "ab" * 16 and units["Daikin_a"].name == "Bedroom"
    assert units["Daikin_b"].key == "cd" * 16
    assert cloud.normalise_region("MY") == "DM"
    assert cloud.normalise_region(None) == "DM"
    assert cloud.normalise_region("DSP") == "DSP"

    bad = cloud.classify_http_error(
        400, '{"message":"NotAuthorizedException","error":"IncorrectUsernameOrPassword"}', "u"
    )
    assert isinstance(bad, cloud.CloudAuthError)
    assert isinstance(cloud.classify_http_error(403, "", "u"), cloud.CloudAuthError)
    other = cloud.classify_http_error(502, "Bad Gateway", "u")
    assert isinstance(other, cloud.CloudError) and not isinstance(other, cloud.CloudAuthError)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print("ok ", test.__name__)
    print(f"{len(tests)} tests passed")
