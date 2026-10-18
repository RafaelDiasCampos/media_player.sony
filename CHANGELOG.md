# Changelog

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
