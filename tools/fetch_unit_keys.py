#!/usr/bin/env python3
"""One-time fetch of the per-unit AES keys from your own GO DAIKIN account.

Reproduces exactly what the GO DAIKIN app does at login (decompiled bundle, module 3154
`login` saga and the `fetchUnitShadowState` saga):

    POST {UNIVERSAL_API}/universallogin        {"requestData":{"username":..,"password":..}}
      -> AccessToken, IdToken, RefreshToken, IdentityId, userID, activeRegion, ...
    POST {API_URL(region)}/gethomepageshadowstate  {"requestData":{"userID":..}}
      header Authorization: <IdToken>           -> {"units":[{"thingName":..,"shadowState":{..,"key":..}}]}

Run it yourself (so the password never passes through a chat log):

    python3 tools/fetch_unit_keys.py                 # prompts for email + password
    python3 tools/fetch_unit_keys.py --save          # also writes ~/.config/godaikin/units.json (mode 600)
    python3 tools/fetch_unit_keys.py --dump          # print the raw API response (debugging)

Only stdlib is used. Nothing is stored unless --save is given.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Public API gateways taken from BuildConfig.java (these are endpoints, not secrets).
UNIVERSAL_API = "https://qr5sjbuvhd.execute-api.ap-southeast-1.amazonaws.com/prod"
REGION_API = {
    "DM": "https://bq53w7rpb6.execute-api.ap-southeast-1.amazonaws.com/prod",   # Malaysia
    "DSP": "https://f32f0yj3k4.execute-api.ap-southeast-1.amazonaws.com/prod",  # Singapore
    "DIN": "https://jm41kogy2b.execute-api.ap-southeast-1.amazonaws.com/prod",  # Indonesia/"INT"
}
UA = "GO DAIKIN/2.2.88 (Android)"


def post(url: str, body: dict, headers: dict | None = None, timeout: float = 20) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": UA, **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw.decode(errors="replace")[:500]
        raise SystemExit(f"HTTP {err.code} from {url}: {detail}") from err
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise SystemExit(f"non-JSON reply from {url}: {raw[:300]!r}")


def unwrap(doc):
    """Lambda proxies sometimes wrap the payload in {"body": "<json string>"} or {"data": {...}}."""
    for _ in range(3):
        if isinstance(doc, dict) and isinstance(doc.get("body"), str):
            try:
                doc = json.loads(doc["body"])
                continue
            except json.JSONDecodeError:
                break
        if isinstance(doc, dict) and "data" in doc and isinstance(doc["data"], (dict, list)) and len(doc) <= 3:
            doc = doc["data"]
            continue
        break
    return doc


def find_units(doc) -> list[dict]:
    """Collect every object that carries a thingName, wherever the response nests it."""
    out: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            if "thingName" in node:
                out.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(doc)
    return out


def key_of(unit: dict) -> str | None:
    for path in (("shadowState", "key"), ("shadowState", "state", "reported", "key"), ("key",)):
        node = unit
        for k in path:
            node = node.get(k) if isinstance(node, dict) else None
        if isinstance(node, str) and node:
            return node
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--username", help="GO DAIKIN login (email). Prompted if omitted.")
    ap.add_argument("--region", choices=sorted(REGION_API), help="Force region instead of using activeRegion from login")
    ap.add_argument("--env-file", default="~/.config/shell/secrets.env",
                    help="file with GODAIKIN_USERNAME= / GODAIKIN_PASSWORD= lines (default: %(default)s)")
    ap.add_argument("--save", action="store_true", help="write ~/.config/godaikin/units.json (chmod 600)")
    ap.add_argument("--dump", action="store_true", help="print raw API responses")
    args = ap.parse_args()

    env = dict(os.environ)
    env_file = Path(args.env_file).expanduser()
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.removeprefix("export ").partition("=")
            env.setdefault(k.strip(), v.strip().strip("'\""))

    username = args.username or env.get("GODAIKIN_USERNAME")
    password = env.get("GODAIKIN_PASSWORD")
    if not sys.stdin.isatty() and not (username and password):
        raise SystemExit(
            "no TTY for prompting. Put these two lines in ~/.config/shell/secrets.env (chmod 600) and rerun:\n"
            "  GODAIKIN_USERNAME=you@example.com\n  GODAIKIN_PASSWORD=...\n"
            "(or pass --env-file <path>)"
        )
    username = username or input("GO DAIKIN email: ").strip()
    password = password or getpass.getpass("GO DAIKIN password (not echoed): ")

    login = unwrap(post(f"{UNIVERSAL_API}/universallogin", {"requestData": {"username": username, "password": password}}))
    if args.dump:
        safe = {k: ("<redacted>" if "Token" in k else v) for k, v in login.items()} if isinstance(login, dict) else login
        print("login response:", json.dumps(safe, indent=2))
    if not isinstance(login, dict) or "IdToken" not in login:
        raise SystemExit(f"login did not return tokens. Response keys: {list(login) if isinstance(login, dict) else type(login)}")

    id_token, access_token = login["IdToken"], login.get("AccessToken")
    user_id = login.get("userID") or login.get("username")
    region = args.region or login.get("activeRegion") or "DM"
    print(f"logged in as {login.get('username', username)}  userID={user_id}  region={region}")

    regions = [region] + [r for r in REGION_API if r != region]
    units: list[dict] = []
    for reg in regions:
        base = REGION_API.get(reg)
        if not base:
            continue
        for token in (id_token, access_token, f"Bearer {id_token}"):
            if not token:
                continue
            try:
                doc = unwrap(post(f"{base}/gethomepageshadowstate", {"requestData": {"userID": user_id}}, {"Authorization": token}))
            except SystemExit as err:
                if args.dump:
                    print(f"[{reg}] {err}")
                continue
            if args.dump:
                print(f"[{reg}] gethomepageshadowstate:", json.dumps(doc, indent=2)[:4000])
            units = find_units(doc)
            if units:
                break
        if units:
            break

    if not units:
        raise SystemExit("no units found in any region; rerun with --dump and share the (redacted) structure")

    rows = []
    print()
    print(f"{'thingName':28} {'name':24} key")
    for u in units:
        key = key_of(u)
        name = u.get("ACName") or u.get("name") or u.get("acName") or ""
        print(f"{u['thingName']:28} {name:24} {key or '(no key field — see --dump)'}")
        rows.append({"thingName": u["thingName"], "name": name, "key": key})

    if args.save:
        out = Path.home() / ".config" / "godaikin" / "units.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rows, indent=2))
        out.chmod(0o600)
        print(f"\nsaved to {out}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
