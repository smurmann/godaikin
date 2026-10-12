# GO DAIKIN 2.2.88 — communication protocols (reverse-engineering notes)

Target: `GO+DAIKIN_2.2.88_APKPure.xapk` (SHA-256 `712ca4ac0088bf10a0801dc17041e7cb9cb42498f7f2a52cded0d4f89b5aa12f`),
package `my.com.daikin.app`, version code 316. Daikin Malaysia / Singapore / Indonesia app.

Method: unpacked the XAPK, decompiled Java with `jadx 1.5.6` (only thin native glue there), and
decompiled the React Native **Hermes bytecode v96** bundle (`assets/index.android.bundle`,
13 MB) with `hermes-dec 0.1.7` into a 109 MB pseudo-JS file (`bundle.js`). Line numbers
below refer to that file. Everything here is a *static* observation; nothing was executed
against a real unit yet, so items marked **UNVERIFIED** need a live test.

## 1. There are three device families, not one

| Family (app name) | Hardware hint | Local protocol | Cloud protocol |
|---|---|---|---|
| **"thingName" air conditioners** (what you want) | Daikin Wi-Fi adapter with AWS IoT identity; firmware `DK` 1_13_16 | UDP discovery + **TCP 7002, AES-128-CBC** | AWS IoT shadow via MQTT/WSS + REST Lambda `publishdevicestate` |
| **"GPF" / Deneb units** | Air purifier / humidifier ("Streamer", PM2.5, odour, water tank); firmware `mc_mcb80` 2_6_408; adapter kinds GainSpan / Marvell / Realtek / STMicro | UDP `DAIKIN_UDP/common/basic_info` on 30050 + **HTTP `/dsiot/multireq`**, no auth | Same DSIoT JSON against `API_URL_DENEB` with OAuth bearer |
| Service/commissioning mode | Technician BLE | BLE GATT service `18aa`, characteristic `2bb2`, plaintext `<{...}>` JSON incl. key | n/a |

Evidence for the split: constants module 1904 (`bundle.js:813900-814330`) defines
`ADAPTER_TYPE_GEN3_MRVL=3`, `ADAPTER_TYPE_GEN4_CJ=4`, `ADAPTER_MAP_THING_TYPE_CJ='DENEB_CJ'`, AP-mode
IPs `192.168.127.1` / `192.168.0.100`, and the whole `dgc_status` property tree is air-purifier
vocabulary (`isUVOn`, `pm25Level`, `odorLevel`, `humidifyCourse`, `isActiveWaterSupplySign`,
`bundle.js:815130-815560`). The air-conditioner vocabulary (`Set_OnOff`, `Set_Mode`, `Set_Temp`,
`Set_Fan`, `Stat_IDRoomTemp`…) lives in the shadow mapper at `bundle.js:754806`.

## 2. Air conditioner — LAN discovery (UDP)

Module 2028 (`bundle.js:847883-850830`) and the UDP helper module 2029 (`bundle.js:850862-851130`).

| Item | Value | Where |
|---|---|---|
| Message | `{"LAN_DEV_DETECT":"LAN_DEV_DETECT"}` | `850780-850790` (concat of `'{"'`,`'LAN_DEV_DETECT'`,`'":"'`,`'LAN_DEV_DETECT'`,`'"}'`) |
| Target | network broadcast address (`NetworkHelper.getBroadcastAddress`), **port 61244** | default in `sendMessage`, `850895-850905` |
| Local bind | `0.0.0.0:61245`, `reuseAddr`, `setBroadcast(true)` | `850895`, `851110` |
| Timeout | 5000 ms, 500 ms settle delay | `850794`, `848000` |
| Reply parsing | `data.slice(0,-1).replace(/'/g,'"')` → `JSON.parse` → `obj["LAN_DEV_DETECT"].thingName`; source IP = unit IP | `848058-848075` |

So the unit answers with a Python-style dict literal (single quotes) plus one trailing
framing character. The reply object at least contains `thingName`.

**Verified live 2026-10-12** against two units on a separate IoT VLAN (Wi‑Fi modules by
Hui Zhou Gaoshengda). Unicast to port 61244 works too; reply comes from source port 61244:

```
{'LAN_DEV_DETECT':{'ACK':'OK','product_name':'AC','thingName':'Daikin_aabbccddeeff'}}\n
```

`thingName` is `Daikin_` + the module's MAC without colons. The units expose only TCP 53
and 7002. Broadcast does not cross VLANs, so `async_discover(targets=[...])` / CLI
`--targets ip,ip` send unicast probes.

Library: `protocol.async_discover()`.

## 3. Air conditioner — LAN control (TCP 7002 + AES)

Module 2148 "wlan_control" (`bundle.js:916869-918908`), native transport `react-native-tcp-socket`
(`createConnection({port, host})`, `916915-916925`), crypto module 2149 (`918908-919182`) built on
Node `crypto.createCipheriv`.

| Item | Value | Where |
|---|---|---|
| Port | **7002** | `r1 = 7002; var _closure1_slot6 = r1` at `918500`, used as `port` in `createClient` |
| Framing | every message is `<` + JSON + `>`; reply has `/\s|<|>/g` stripped before `JSON.parse` | `917205-917215` |
| Ping | `<{"ack": 1}>` with 1000 ms race timeout (`pingUnit`). **Verified:** unit answers `<{"ack":2}>` | `891962-892000`, also sent on pairing `917959-917997` |
| Bad/undecryptable frame | **Verified:** the unit sends nothing back and closes; plaintext `getVersion` is likewise ignored outside provisioning mode | live test |
| Cipher | `aes-128-cbc`, **key = iv = unit key**, key/iv decoded as **hex**, plaintext utf8, ciphertext **hex**, PKCS#7 auto-padding | defaults `utf8`/`hex` at `918930-918940`; `r1['key']=r12; r1['iv']=r12` at `917170-917172` |
| getStatus | plaintext `{"action":"getStatus","version":-1,"thingName":"<thing>"}` → encrypt → send `<{"payload":"<hex>"}>`; reply `<{"payload":"<hex>"}>` → decrypt → `JSON.parse` | `917157-917245` (`getShadowState`, `sendGetStatusCommand`) |
| setStatus | plaintext `{"action":"setStatus","version":-1,"state":{...},"thingName":"<thing>"}` → same wrapping | `917580-917640` |
| Timeouts | 5000 ms command, 3000 ms connect, polling every 1000 ms (`intervalUpdateShadowState`) | `917226-917232`, `917860` |
| Decrypt failure | surfaced as *"Target unit's key might have expired. Please login to get the latest unit's key."* | `917250` |
| Other plaintext frames seen | `<{"action":"getVersion"}>`, `<{"action":"getWifiScanResult","version":-1}>`, `<{"action":"bleconfig","version":-1,"ssid":"…` (provisioning) | string table |

**Verified live 2026-10-12** (firmware `V1.2.3`, two units): the decrypted getStatus reply is a
**flat** document, no shadow envelope:

```json
{"updatedOn":"2026-10-1212:05:04","thingName":"Daikin_aabbccddeeff","version":"V1.2.3",
 "eventType":"connected","ota_flag":0,"remote_ota_flag":0,"d_ota_flag":0,"ip":"","port":"",
 "timerState":0,"rbt":0,"cfg":0,"sch":1,
 "Set_OnOff":0,"Set_Fan":4,"Set_Mode":1,"Set_Temp":21,"Set_Turbo":0,"Set_Silent":0,
 "Set_Sleep":0,"Set_Ion":1,"Set_Swing":0,"Set_UDLvr":0}
```

setStatus is acknowledged with an encrypted document of the same shape echoing the fields
that were set (plus the metadata, `sch` 0). The unit applies the value immediately; a follow-up
getStatus shows it. Observations worth knowing:

* **No sensor values** (`Stat_IDRoomTemp`, outdoor temp, humidity, power) appear in the local
  document on this firmware. Those exist only in the cloud shadow. The HA sensors therefore
  stay unavailable; the climate entity has no current temperature.
* `Set_FanExtend` is absent → these are 3-speed handsets (`Set_Fan` 2/4/8, 128 auto).
  `protocol.is_five_speed()` keys off that.
* Numbers are sent and returned as JSON integers.

### 3.1 State vocabulary and value tables

Field names are the AWS IoT shadow keys (mapper at `754806`, `755560-755660`).

| Field | Meaning | Values | Evidence |
|---|---|---|---|
| `Set_OnOff` | power | `0` / `1` | `1043155-1043160` |
| `Set_Mode` | mode | `1` cool, `2` fan, `4` dry, `8` heat, `10` auto | ModeKeyMap module 1783, `756300-756330` |
| `Set_Temp` | setpoint °C, integer | ranges `{min:16,max:30}` (also 18–30/18–32 per model) | `1043225`, min/max tables |
| `Set_Fan` + `Set_FanExtend` | fan (5-speed handset "DAMA") | auto `128`; L1 `2_0`; L2 `2_1`; L3 `4_0`; L4 `8_1`; L5 `8_0` (`Set_Fan_Set_FanExtend`) | module 1765, `753318-753400` |
| `Set_Fan` (3-speed handset "DIL") | fan | auto `128`, `2`, `4`, `8` | same module |
| `Set_Swing` + `Set_UDLvr` | vertical louver | `0_0` off, `0_1`…`0_5` fixed positions, `1_15` full swing | module 1788, `757440-757500` |
| `Set_Silent`, `Set_Turbo`, `Set_Breeze`, `Set_Ecoplus`, `Set_Streamer`, `Set_LEDOff`… | feature toggles | `0`/`1` | `getFanSpeedPayload` `1070588` |
| `Stat_IDRoomTemp`, `Stat_ODAirTemp`, `Sta_IDRh`, `Stat_ErrorCode`, `Stat_ODPowerConsumption` | sensors | numbers (as strings in some docs) | `754806` |
| `key` | **the unit's AES key is itself a shadow field** | hex string | `754806` (`'key': 'key'`) |

### 3.2 Where the key comes from

The key is not derivable locally. The app receives it from the cloud:

* `gethomepageshadowstate` (REST on `API_URL`, response `units[].shadowState`) — `1064716`,
* `getpaireddevices` (response rows carry `key`, `qx`, `expiryTime`) — `843599`, `845361`,
* and the live shadow documents on MQTT topic `$aws/things/<thing>/shadow/update/documents` — `1027336`.

Practical ways to obtain it once (then the cloud is never needed again):

1. **The Home Assistant config flow** ("Sign in with GO DAIKIN account") or **`tools/fetch_unit_keys.py`**. Both replay the app's own login, which is a
   plain HTTPS call to the vendor's Lambda, no Cognito client secret required
   (module 3154 `login` saga, `bundle.js:1388860-1388930`):

   ```
   POST {UNIVERSAL_API}/universallogin
        {"requestData":{"username":"<email>","password":"<pw>"}}
   -> {AccessToken, IdToken, RefreshToken, IdentityId, ExpiresIn, username, timezone, activeRegion, userID}

   POST {API_URL for activeRegion}/gethomepageshadowstate     (fetchUnitShadowState, 1064646-1064721)
        header Authorization: <IdToken>        (buildAuthHeader: IdToken by default, AccessToken for
                                               getidentityid/switchcountry/federatedcheck, 1106560-1106610)
        {"requestData":{"userID":"<userID>"}}
   -> {"units":[{"thingName":..,"shadowState":{..,"key":"<hex>",..}}, ...]}
   ```

   `activeRegion` selects the gateway: `DM` Malaysia, `DSP` Singapore, `DIN` Indonesia
   (`BuildConfig.SERVER/SG_SERVER/INT_SERVER`). The script prompts for the password
   itself so it never lands in a shell history or chat log.
2. **Capture it from the app** with mitmproxy on the phone; the same
   `gethomepageshadowstate` response, or the MQTT shadow `documents` message, carries `key`.

## 4. Air conditioner — cloud path (for completeness)

* Auth: AWS Cognito (user pool + identity pool), Amplify; universal login Lambdas on
  `UNIVERSAL_API` (`universallogin`, `refreshtoken`, …) — `1106360-1106925`.
* Telemetry: AWS IoT Core MQTT over WebSocket (`iotdevicegateway`, endpoint
  `*-ats.iot.ap-southeast-1.amazonaws.com`), subscribes to
  `$aws/things/<thing>/shadow/update/documents` and `/shadow/update/accepted`.
* Commands: REST POST `publishdevicestate` with `{thingName, state:{desired:{Set_*}}}`
  (`1039600-1039800`), `setdevicestatus`, `getdevicedetail`, `getpaireddevices`, `unpairdevicedg`…
* Timers: AppSync GraphQL (`ListUserTimer`, `CreateTimer`, …).

## 5. GPF / Deneb air purifier — local HTTP (DSIoT)

If you ever add a Daikin Streamer purifier, this is a fully unauthenticated local API:

* Discovery: UDP `DAIKIN_UDP/common/basic_info` to broadcast **:30050** from local **:30000**,
  reply `k=v,k=v,...` (`ret`, `type`, `ver`, `ssid`, `name` (URI-encoded), `edid`, `mode` ap/run)
  → `thingName = THING_TYPE + "_" + edid` — `848135-848300`.
* Control: `POST http://<ip>/dsiot/multireq` with
  `{"requests":[{"op":2,"to":"/dsiot/edge/adr_0100.dgc_status","rqi":0}]}`;
  `op` 2 = GET, 3 = UPDATE, 4 = DELETE; writes carry
  `"pc":{"pn":"dgc_status","pch":[{"pn":"e_1002","pch":[{"pn":"e_A002","pch":[{"pn":"p_01","pv":"01"}]}]}]}`
  (`pv` is hex: `"01"` on / `"00"` off) — modules 1921–1936, `819370-822600`; client
  `ApiServiceDeneb.getAdapterEndpoint = 'http://' + ip` (`1100705`), `postWithoutAuth`.
* Status paths: power `e_1002/e_A002/p_01`, indoor temp `e_1002/e_A00B/p_01` (byte, offset 128
  handling at `815376`), humidity `e_A00B/p_02`, PM2.5 `e_A00B/p_21`, odour level `e_3007/p_1F`, …
* Adapter config: `/config/adapter/user_info` (bind `id_token` + `method` polling|home_only),
  `/config/wlan/settings`, `/config/wlan/scan/start`, `/config/adapter/timezone`,
  `/config/firmware/update` (multipart, Basic auth) — `851880-852270`.

## 6. Things the app embeds that you should know about

`BuildConfig.java` contains the production Cognito client IDs **and secrets**, Alexa client
secret, Google Maps key, two static AES keys/IVs (`QRCODE_*`, `STORAGE_ENCRYPTION_*`) and the
Bugsnag/Transistorsoft keys. They are the vendor's, not yours; this repo deliberately does
not copy them into code.

## 7. Open questions to settle with one live session

1. ~~Does the unit answer discovery on 61244?~~ Yes; reply carries `ACK`, `product_name`, `thingName` only.
2. ~~Exact getStatus reply envelope.~~ Flat document, see §3.
3. ~~Whether the unit acks setStatus.~~ Encrypted echo of the set fields.
4. ~~`Set_Temp` number or string.~~ Integer works (set 21 → echoed 21).
5. 5-speed fan pairing (`level2 = 2_1`, `level4 = 8_1`) remains untested: both test units are 3-speed.
6. Does the local document ever include `Stat_IDRoomTemp` (e.g. while running), or is room
   temperature cloud-only on `V1.2.3`? Check after the first real cooling session.
7. Does the per-unit key survive app re-login / re-pairing? Keep the fetch script handy.

`tools/godaikin_cli.py` has `discover`, `ping`, `status`, `set` and `raw` subcommands for exactly this.
