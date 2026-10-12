# CLAUDE.md

Local-only Home Assistant integration for GO DAIKIN air conditioners. The protocol was
reverse-engineered from the app; `docs/PROTOCOL.md` is the evidence trail and lists which
behaviour is verified on hardware and which is still inferred. Read it before changing
`custom_components/godaikin_local/protocol.py`.

## Working rules

- `python3 tools/test_protocol.py` must pass before any commit. It is stdlib + `cryptography` only.
- Never commit unit keys, credentials or the vendor APK. `.gitignore` covers `units.json`, `*.env`, `*.apk`, `*.xapk`.
- Vendor API endpoints may appear in code; vendor client secrets from the APK must not.
- Live testing needs a unit on the LAN and its key (`tools/fetch_unit_keys.py`). Prefer read-only
  `status` calls; a `set` that repeats the current value is the safe way to probe acknowledgements.

## Agent skills

### Issue tracker

GitHub Issues on this repo via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
