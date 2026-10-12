<p align="center"><img src="docs/icon.png" width="128" alt="GO DAIKIN Local"></p>

# GO DAIKIN Local

Cloud-free Home Assistant integration for Daikin air conditioners that are normally driven by
the **GO DAIKIN** app (Daikin Malaysia, Singapore and Indonesia; package `my.com.daikin.app`).

Home Assistant talks to the unit over your LAN using the same protocol the app uses when the
phone is at home: UDP discovery on port 61244 and an AES-encrypted JSON channel on TCP 7002.
The cloud is contacted exactly once, during setup, to obtain the per-unit key.
[docs/PROTOCOL.md](docs/PROTOCOL.md) documents how the protocol was recovered from the app and
which behaviour has been verified on real units.

## Features

| Entity | What it does |
|---|---|
| `climate` | Off / cool / dry / fan only / heat / auto, target temperature 16–30 °C, fan speed (`auto`, `low`, `medium`, `high`, plus `medium_low` / `medium_high` on 5-speed units), swing (`off` / `vertical`), presets (`boost`, `silent`, `sleep`) |
| `select` Louver position | Fixed vertical louver positions 1–5, off, or swing |
| `switch` Ioniser | The unit's ioniser / streamer |
| `sensor` | Firmware version, plus room/outdoor temperature, humidity, power and error code **when the unit reports them** (adapter firmware V1.2.3 does not) |

Re-authentication is built in: if a unit starts rejecting its key, Home Assistant prompts you
to sign in again and refreshes it.

## Installation

### HACS (recommended)

1. HACS → Integrations → ⋮ → *Custom repositories* → add `https://github.com/smurmann/godaikin-local` as an *Integration*.
2. Install **GO DAIKIN Local** and restart Home Assistant.

### Manual

Copy `custom_components/godaikin_local` into your `config/custom_components/` and restart.

## Setup

*Settings → Devices & services → Add integration → GO DAIKIN Local*, then either:

- **Sign in with GO DAIKIN account** (recommended): enter the email and password you use in the
  app. The integration fetches the key for each unit on the account, lets you pick one, and asks
  for its IP address (pre-filled when the unit is on the same network as Home Assistant). Your
  password is not stored. Repeat for each unit.
- **Enter unit key manually**: IP, thing name and the 32-character hex key, for example from
  `tools/fetch_unit_keys.py` below.

Give the units a DHCP reservation; the integration addresses them by IP.

## Command-line tools

```bash
python3 tools/test_protocol.py                       # offline tests
python3 tools/godaikin_cli.py discover               # UDP broadcast on this network
python3 tools/godaikin_cli.py --targets 192.168.20.7,192.168.20.8 discover   # units on another VLAN
python3 tools/fetch_unit_keys.py                     # keys from your account (prompts; --save to store)
export GODAIKIN_KEY=<32 hex chars>
python3 tools/godaikin_cli.py status --host 192.168.x.y --thing <thingName>
python3 tools/godaikin_cli.py set    --host 192.168.x.y --thing <thingName> Set_OnOff=1 Set_Mode=1 Set_Temp=24
```

The tools need only the Python standard library plus `cryptography`.

## Network notes

- Discovery is a UDP broadcast and will not cross VLANs. If Home Assistant and the units are on
  different networks, enter the IP by hand; control itself is plain unicast TCP and routes fine.
- Anyone on your LAN who holds a unit's key can control that unit. Treat keys like passwords.

## Status and known limitations

Verified live on two units (adapter firmware V1.2.3): discovery, encrypted status and control,
and the Home Assistant entities. Not yet seen on hardware: the 5-speed fan table and any firmware
that reports sensor values locally. Open questions are tracked in
[PROTOCOL.md §7](docs/PROTOCOL.md).

## Brand assets

`brands/custom_integrations/godaikin_local/` holds the icon and logo in the layout the
[home-assistant/brands](https://github.com/home-assistant/brands) repository expects. Home
Assistant only shows integration logos served from that repository, so they appear in the UI
once a pull request adding this folder is merged there.

## Development

```bash
pip install ruff cryptography
ruff check . && ruff format --check .
python3 tools/test_protocol.py
```

CI runs hassfest, the HACS validator, ruff and the tests on every push.
