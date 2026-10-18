# Changelog

## 2.2.0

- Discover compatible Sony CERS/IRCC devices using Home Assistant SSDP/UPnP,
  including their advertised description URLs and ports.
- Match configured devices by MAC address from DHCP discovery or by observed
  MAC/UPnP identity from SSDP, then reconnect automatically at the new IP.
- Reuse pairing credentials after address changes, including host-scoped cookies,
  and keep existing config entries and entity identities stable.
- Confirm newly discovered devices before starting registration and avoid
  duplicate entries or pairing prompts for already configured hardware.
- Read device identity without saved credentials before reconnecting, and reject
  a contradictory MAC address instead of treating another Sony as the same unit.
- Add Home Assistant regression tests for DHCP and SSDP address changes, UPnP
  identity fallback, registration reuse, duplicate matching and discovered setup.

## 2.1.1

- Reuse the registered client and saved authentication cookies on setup and
  reload instead of trying to register again with a consumed, single-use PIN.
- Retain cookies returned by legacy CERS registration, including their scope
  and expiry, and persist them through the config entry.
- Validate saved authorization against a fresh command list; revoked credentials
  still open reauthentication instead of silently accepting stale commands.
- Add full Home Assistant pairing-to-setup and reload regression tests for
  one-time PIN devices, with and without an authentication cookie.
- Publish a versioned GitHub release after validation so HACS can download a tag.

## 2.1.0

- Add HACS metadata, an issue tracker, local brand assets and validation workflows.
- Update setup and entity lifecycle to use typed config-entry runtime data.
- Preserve existing config entries and entity unique IDs.
- Require successful registration before saving; keep one client across PIN steps.
- Add reauthentication for expired registrations, including PINs with leading zeros.
- Share polling and serialize synchronous Sony requests in the executor.
- Keep IRCC authentication headers and report failed requests to service callers.
- Distinguish confirmed Scalar standby from connection and authentication failures.
- Fix media state types, remote power state and unsupported capability flags.
- Support remote sequences with asynchronous delays and validate all commands first.
- Add English, French and Brazilian Portuguese setup translations.
- Replace obsolete Travis checks and saved Home Assistant state with current tests.

Validation uses Home Assistant 2026.10.2 and Python 3.14 with mocked Sony HTTP
responses. This release has not been tested on physical Sony hardware.
