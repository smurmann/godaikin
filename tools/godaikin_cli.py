#!/usr/bin/env python3
"""Command-line tester for the GO DAIKIN local protocol.

Examples
    godaikin_cli.py discover
    godaikin_cli.py ping   --host 192.168.1.50
    godaikin_cli.py status --host 192.168.1.50 --thing <thingName> --key <32 hex chars>
    godaikin_cli.py set    --host ... --thing ... --key ... Set_OnOff=1 Set_Mode=1 Set_Temp=24
    godaikin_cli.py raw    --host ... '<{"action":"getVersion"}>'     # unencrypted probe

Keep the key out of your shell history: export GODAIKIN_KEY=... and omit --key.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

try:
    from _load import load
except ImportError:  # imported as a package from the repo root
    from tools._load import load

protocol = load("protocol")


def _unit(args: argparse.Namespace) -> protocol.DaikinLocalUnit:
    key = args.key or os.environ.get("GODAIKIN_KEY")
    if not key:
        sys.exit("need --key or GODAIKIN_KEY")
    if not args.thing:
        sys.exit("need --thing")
    return protocol.DaikinLocalUnit(args.host, args.thing, key, args.port)


async def cmd_discover(args: argparse.Namespace) -> None:
    targets = [t.strip() for t in args.targets.split(",")] if args.targets else None
    units = await protocol.async_discover(broadcast=args.broadcast, listen_for=args.timeout, targets=targets)
    if not units:
        print(
            "no replies (same L2 network? otherwise pass --targets <ip>,<ip>; "
            "is UDP 61245 free and allowed by the firewall?)"
        )
    for u in units:
        print(f"{u.thing_name}\t{u.host}\t{json.dumps(u.raw)}")


async def cmd_ping(args: argparse.Namespace) -> None:
    unit = protocol.DaikinLocalUnit.__new__(protocol.DaikinLocalUnit)
    unit.host, unit.port = args.host, args.port
    raw = await unit._exchange(protocol.PING_MESSAGE.encode(), read_timeout=2.0)
    print(raw.decode("utf-8", errors="replace"))


async def cmd_status(args: argparse.Namespace) -> None:
    unit = _unit(args)
    raw = await unit._exchange(protocol.build_get_status(unit.thing_name, unit.unit_key))
    print("raw reply :", raw.decode("utf-8", errors="replace")[:400])
    reply = protocol.parse_encrypted_reply(raw, unit.unit_key)
    print("decrypted :", json.dumps(reply, indent=2))


async def cmd_set(args: argparse.Namespace) -> None:
    unit = _unit(args)
    state = {}
    for kv in args.fields:
        k, _, v = kv.partition("=")
        try:
            state[k] = int(v)
        except ValueError:
            state[k] = v
    print("sending   :", json.dumps(state))
    print("reply     :", json.dumps(await unit.async_set_status(state), indent=2))


async def cmd_raw(args: argparse.Namespace) -> None:
    unit = protocol.DaikinLocalUnit.__new__(protocol.DaikinLocalUnit)
    unit.host, unit.port = args.host, args.port
    raw = await unit._exchange(args.message.encode(), read_timeout=args.timeout)
    print(raw.decode("utf-8", errors="replace"))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host")
    p.add_argument("--port", type=int, default=protocol.CONTROL_PORT)
    p.add_argument("--thing")
    p.add_argument("--key")
    p.add_argument("--timeout", type=float, default=5.0)
    p.add_argument("--broadcast", default="255.255.255.255")
    p.add_argument(
        "--targets", default=None, help="comma-separated unicast discovery IPs (units on another VLAN)"
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover")
    sub.add_parser("ping")
    sub.add_parser("status")
    s = sub.add_parser("set")
    s.add_argument("fields", nargs="+", help="Field=value, e.g. Set_OnOff=1 Set_Temp=24")
    r = sub.add_parser("raw")
    r.add_argument("message")
    args = p.parse_args()
    if args.cmd != "discover" and not args.host:
        p.error("--host is required")
    asyncio.run(
        {"discover": cmd_discover, "ping": cmd_ping, "status": cmd_status, "set": cmd_set, "raw": cmd_raw}[
            args.cmd
        ](args)
    )


if __name__ == "__main__":
    main()
