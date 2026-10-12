#!/usr/bin/env python3
"""One-time fetch of the per-unit AES keys from your own GO DAIKIN account (stdlib only).

The Home Assistant config flow does this for you; this tool exists for scripting and for
the manual setup path.  It replays the app's own login (docs/PROTOCOL.md §3.2):

    POST {UNIVERSAL_API}/universallogin            {"requestData":{"username","password"}}
    POST {API_URL[region]}/gethomepageshadowstate   {"requestData":{"userID"}}   Authorization: IdToken

Credentials come from (in order) --username / prompts, the environment variables
GODAIKIN_USERNAME / GODAIKIN_PASSWORD, or an env file (default ~/.config/shell/secrets.env).
Nothing is stored unless --save is given.
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

from _load import load

_cloud = load("cloud")
REGION_API, UNIVERSAL_API, USER_AGENT = _cloud.REGION_API, _cloud.UNIVERSAL_API, _cloud.USER_AGENT
find_units, normalise_region, unwrap = _cloud.find_units, _cloud.normalise_region, _cloud.unwrap


def post(url: str, body: dict, headers: dict | None = None, timeout: float = 20) -> object:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")[:500]
        raise SystemExit(f"HTTP {err.code} from {url}: {detail}") from err
    try:
        return unwrap(json.loads(raw))
    except json.JSONDecodeError as err:
        raise SystemExit(f"non-JSON reply from {url}: {raw[:300]!r}") from err


def load_env(env_file: Path) -> dict[str, str]:
    env = dict(os.environ)
    if env_file.is_file():
        for raw_line in env_file.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.removeprefix("export ").partition("=")
            env.setdefault(key.strip(), value.strip().strip("'\""))
    return env


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--username", help="GO DAIKIN login (email)")
    ap.add_argument("--region", choices=sorted(REGION_API), help="force region instead of activeRegion")
    ap.add_argument("--env-file", default="~/.config/shell/secrets.env", help="default: %(default)s")
    ap.add_argument("--save", action="store_true", help="write ~/.config/godaikin/units.json (chmod 600)")
    ap.add_argument("--dump", action="store_true", help="print raw API responses (tokens redacted)")
    args = ap.parse_args()

    env = load_env(Path(args.env_file).expanduser())
    username = args.username or env.get("GODAIKIN_USERNAME")
    password = env.get("GODAIKIN_PASSWORD")
    if not sys.stdin.isatty() and not (username and password):
        raise SystemExit(
            "no TTY for prompting. Put GODAIKIN_USERNAME= and GODAIKIN_PASSWORD= in "
            f"{args.env_file} (chmod 600) or export them, then rerun."
        )
    username = username or input("GO DAIKIN email: ").strip()
    password = password or getpass.getpass("GO DAIKIN password (not echoed): ")

    login = post(
        f"{UNIVERSAL_API}/universallogin", {"requestData": {"username": username, "password": password}}
    )
    if not isinstance(login, dict) or "IdToken" not in login:
        raise SystemExit(f"login did not return tokens: {login if args.dump else type(login).__name__}")
    if args.dump:
        print(
            "login:",
            json.dumps({k: ("<redacted>" if "Token" in k else v) for k, v in login.items()}, indent=2),
        )

    user_id = str(login.get("userID") or login.get("username") or username)
    region = args.region or normalise_region(login.get("activeRegion"))
    print(f"logged in as {login.get('username', username)}  userID={user_id}  region={region}")

    units = []
    for reg in [region, *[r for r in REGION_API if r != region]]:
        for token in (login["IdToken"], login.get("AccessToken")):
            if not token:
                continue
            try:
                doc = post(
                    f"{REGION_API[reg]}/gethomepageshadowstate",
                    {"requestData": {"userID": user_id}},
                    {"Authorization": token},
                )
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
        raise SystemExit("no units found; rerun with --dump and inspect the structure")

    print()
    print(f"{'thingName':28} {'name':24} key")
    rows = []
    for unit in units:
        print(f"{unit.thing_name:28} {unit.name:24} {unit.key or '(no key field)'}")
        rows.append({"thingName": unit.thing_name, "name": unit.name, "key": unit.key})

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
