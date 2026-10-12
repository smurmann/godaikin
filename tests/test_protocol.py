"""Run the protocol unit tests (tools/test_protocol.py) under the package import path for coverage."""

from __future__ import annotations

import importlib

from custom_components.godaikin_local import cloud, protocol

_suite = importlib.import_module("tools.test_protocol")
_suite.p = protocol
_suite.cloud = cloud

test_crypto_roundtrip = _suite.test_crypto_roundtrip
test_known_vector = _suite.test_known_vector
test_frames = _suite.test_frames
test_reply_parsing = _suite.test_reply_parsing
test_discovery_parse = _suite.test_discovery_parse
test_value_maps = _suite.test_value_maps
test_cloud_parsing = _suite.test_cloud_parsing
