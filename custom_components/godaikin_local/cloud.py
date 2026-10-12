"""One-time GO DAIKIN cloud login used only to obtain each unit's local AES key.

Mirrors the app's own login path (see docs/PROTOCOL.md §3.2):

    POST {UNIVERSAL_API}/universallogin   {"requestData": {"username", "password"}}
    POST {API_URL[region]}/gethomepageshadowstate  {"requestData": {"userID"}}  (Authorization: IdToken)

No Cognito client secrets are needed and nothing is persisted; the integration keeps
only the per-unit keys it gets back.  After that, all traffic is LAN-only.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiohttp

_LOGGER = logging.getLogger(__name__)

# Public API gateways from the app's BuildConfig (endpoints, not secrets).
UNIVERSAL_API = "https://qr5sjbuvhd.execute-api.ap-southeast-1.amazonaws.com/prod"
REGION_API: dict[str, str] = {
    "DM": "https://bq53w7rpb6.execute-api.ap-southeast-1.amazonaws.com/prod",  # Malaysia
    "DSP": "https://f32f0yj3k4.execute-api.ap-southeast-1.amazonaws.com/prod",  # Singapore
    "DIN": "https://jm41kogy2b.execute-api.ap-southeast-1.amazonaws.com/prod",  # Indonesia
}
# Observed live: the login reply says "MY" where BuildConfig says "DM".
REGION_ALIASES = {"MY": "DM", "SG": "DSP", "ID": "DIN", "IN": "DIN"}
USER_AGENT = "GO DAIKIN/2.2.88 (Android)"
TIMEOUT_SECONDS = 30


class CloudError(Exception):
    """Transport or unexpected-response error."""


class CloudAuthError(CloudError):
    """Wrong credentials."""


@dataclass(slots=True)
class CloudUnit:
    thing_name: str
    key: str | None
    name: str
    raw: dict[str, Any]


@dataclass(slots=True)
class CloudSession:
    id_token: str
    access_token: str | None
    user_id: str
    region: str
    username: str


def unwrap(doc: Any) -> Any:
    """Lambda proxies sometimes wrap payloads as {"body": "<json>"} or {"data": {...}}."""
    for _ in range(3):
        if isinstance(doc, dict) and isinstance(doc.get("body"), str):
            try:
                doc = json.loads(doc["body"])
                continue
            except json.JSONDecodeError:
                break
        if isinstance(doc, dict) and isinstance(doc.get("data"), (dict, list)) and len(doc) <= 3:
            doc = doc["data"]
            continue
        break
    return doc


def find_units(doc: Any) -> list[CloudUnit]:
    """Collect every object carrying a thingName, wherever the response nests it."""
    out: list[CloudUnit] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "thingName" in node:
                out.append(
                    CloudUnit(
                        thing_name=str(node["thingName"]),
                        key=key_of(node),
                        name=str(node.get("ACName") or node.get("name") or node.get("acName") or ""),
                        raw=node,
                    )
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(doc)
    # De-duplicate by thingName, preferring entries that carry a key.
    best: dict[str, CloudUnit] = {}
    for unit in out:
        if unit.thing_name not in best or (unit.key and not best[unit.thing_name].key):
            best[unit.thing_name] = unit
    return list(best.values())


def key_of(unit: dict[str, Any]) -> str | None:
    for path in (("shadowState", "key"), ("shadowState", "state", "reported", "key"), ("key",)):
        node: Any = unit
        for part in path:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, str) and node:
            return node
    return None


def normalise_region(region: str | None) -> str:
    region = (region or "DM").upper()
    return REGION_ALIASES.get(region, region)


# Error codes the login Lambda returns with HTTP 400 for bad credentials (observed live:
# {"message":"NotAuthorizedException","error":"IncorrectUsernameOrPassword"}).
AUTH_ERROR_MARKERS = (
    "NotAuthorizedException",
    "IncorrectUsernameOrPassword",
    "UserNotFoundException",
    "UserNotConfirmedException",
    "UserNotRegisteredException",
    "EmailNotRegisteredException",
)


def classify_http_error(status: int, text: str, url: str) -> CloudError:
    """Map an HTTP error response to CloudAuthError (bad credentials) or CloudError."""
    if status in (401, 403) or any(marker in text for marker in AUTH_ERROR_MARKERS):
        return CloudAuthError(f"HTTP {status}: {text[:120]}")
    return CloudError(f"HTTP {status} from {url}: {text[:200]}")


class GoDaikinCloud:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def _post(self, url: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
        import aiohttp  # noqa: PLC0415 - lazy so tools/ can import the pure helpers without aiohttp

        hdrs = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT}
        if headers:
            hdrs.update(headers)
        timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
        try:
            async with self._session.post(url, json=body, headers=hdrs, timeout=timeout) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise classify_http_error(resp.status, text, url)
        except aiohttp.ClientError as err:
            raise CloudError(str(err)) from err
        try:
            return unwrap(json.loads(text))
        except json.JSONDecodeError as err:
            raise CloudError(f"non-JSON reply from {url}") from err

    async def async_login(self, username: str, password: str) -> CloudSession:
        doc = await self._post(
            f"{UNIVERSAL_API}/universallogin", {"requestData": {"username": username, "password": password}}
        )
        if not isinstance(doc, dict) or "IdToken" not in doc:
            message = doc.get("message") if isinstance(doc, dict) else None
            raise CloudAuthError(str(message or "login did not return tokens"))
        return CloudSession(
            id_token=doc["IdToken"],
            access_token=doc.get("AccessToken"),
            user_id=str(doc.get("userID") or doc.get("username") or username),
            region=normalise_region(doc.get("activeRegion")),
            username=str(doc.get("username") or username),
        )

    async def async_get_units(self, session: CloudSession) -> list[CloudUnit]:
        regions = [session.region, *[r for r in REGION_API if r != session.region]]
        last_error: Exception | None = None
        for region in regions:
            base = REGION_API.get(region)
            if not base:
                continue
            for token in (session.id_token, session.access_token):
                if not token:
                    continue
                try:
                    doc = await self._post(
                        f"{base}/gethomepageshadowstate",
                        {"requestData": {"userID": session.user_id}},
                        {"Authorization": token},
                    )
                except CloudError as err:
                    last_error = err
                    continue
                units = find_units(doc)
                if units:
                    return units
        if last_error:
            raise CloudError(f"could not list units: {last_error}")
        return []
