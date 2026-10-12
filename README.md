# godaikin-local

Cloud-free Home Assistant integration for Daikin air conditioners that are normally driven by
the **GO DAIKIN** app (Daikin Malaysia / Singapore / Indonesia, package `my.com.daikin.app`).

It speaks the same LAN protocol the app uses when phone and unit share a Wi-Fi network:
UDP discovery on port 61244 and an AES-128-CBC encrypted JSON channel on TCP port 7002.
How that was recovered from the app is documented in [docs/PROTOCOL.md](docs/PROTOCOL.md).

> Status: protocol reconstructed from the decompiled app and **verified live on 2026-10-12**
> against two units (firmware V1.2.3): discovery, ping, encrypted getStatus and setStatus all
> work. The Home Assistant entities themselves have not yet been exercised inside HA.
> Limitation on this firmware: the local status has no room-temperature or power readings,
> so the climate entity shows no current temperature and the sensors stay unavailable.

## Layout

```
custom_components/godaikin_local/   Home Assistant integration (climate + sensors)
  protocol.py                       pure-Python protocol: discovery, framing, AES, value tables
tools/godaikin_cli.py               discover / ping / status / set / raw against a unit
tools/test_protocol.py              offline self-tests (python3 tools/test_protocol.py)
docs/PROTOCOL.md                    evidence and open questions
```

## What you need

1. The unit's **IP address** (DHCP reservation recommended) and **thingName** — `discover` gives both.
2. The unit's **key**: a 32-character hex string that the GO DAIKIN cloud sends to the app.
   It is per unit and, as far as the app shows, stable until you re-pair. Fetch it once with
   your own account (the script prompts for the password; nothing is stored unless `--save`):

   ```bash
   python3 tools/fetch_unit_keys.py            # prints thingName + key per unit
   python3 tools/fetch_unit_keys.py --save     # also writes ~/.config/godaikin/units.json (600)
   ```

   Details and the mitmproxy alternative: [PROTOCOL.md §3.2](docs/PROTOCOL.md).

## Try it from the shell first

```bash
cd godaikin-local
python3 tools/test_protocol.py                       # offline sanity
python3 tools/godaikin_cli.py discover               # UDP broadcast, lists thingName + IP
python3 tools/godaikin_cli.py --targets 192.168.20.7,192.168.20.8 discover   # units on another VLAN
python3 tools/godaikin_cli.py ping   --host 192.168.x.y
export GODAIKIN_KEY=<32 hex chars>
python3 tools/godaikin_cli.py status --host 192.168.x.y --thing <thingName>
python3 tools/godaikin_cli.py set    --host 192.168.x.y --thing <thingName> Set_OnOff=1 Set_Mode=1 Set_Temp=24
```

`status` prints the raw frame and the decrypted document. If decryption fails the key is wrong.

## Install in Home Assistant

Copy `custom_components/godaikin_local` into your HA `config/custom_components/`, restart,
then *Settings → Devices & services → Add integration → GO DAIKIN Local*. The flow broadcasts
for units, lets you pick one, and asks for the key. It validates by performing a real
`getStatus` before saving.

Entities per unit:

- `climate`: off / cool / dry / fan_only / heat / auto, target temperature 16–30 °C,
  fan `auto`, `level1`…`level5` (or 3 levels), swing `off` / `swing` / `position1`…`position5`.
- sensors: room temperature, outdoor temperature, humidity, power, error code (only those the
  unit actually reports).

Polling interval is 30 s (the app polls every second; no need to hammer it).

## Known unknowns

Listed in [PROTOCOL.md §7](docs/PROTOCOL.md). The most likely thing to need a tweak is
`protocol.extract_state()` once we see a real `getStatus` reply.
