# Sony Legacy API

Control Sony devices that expose the legacy CERS/IRCC network API from Home
Assistant. The integration creates a media player and a remote for each device.

Requires **Home Assistant 2026.10.0 or newer**. Automated tests run against Home
Assistant **2026.10.2 on Python 3.14**, using simulated Sony HTTP responses.
Physical-device testing is still required for individual models and firmware.

## Install with HACS

1. Open **HACS**, then **⋮ > Custom repositories**.
2. Add `https://github.com/RafaelDiasCampos/media_player.sony` with type **Integration**.
3. Find **Sony Legacy API**, open it and select **Download**.
4. Restart Home Assistant.
5. Open **Settings > Devices & services > Add integration** and search for
   **Sony Legacy API**.

This fork includes the updated code (integration version **2.1.0**) on its
`master` branch. The older
`c264add` revision lacks required HACS metadata and remains incompatible with
HACS. Add this fork's URL to HACS, as shown above.

This repository can be installed as a **custom repository**; inclusion in HACS's
default catalog is a separate submission. A GitHub release is optional for custom
repositories. Without releases, HACS downloads the default branch.

### Manual installation

Copy the complete `custom_components/sony` directory into
`<config_directory>/custom_components/sony`, then restart Home Assistant. The
`manifest.json` file must be directly inside that directory. Add the integration
through **Settings > Devices & services**.

## Pair a device

Keep Home Assistant and the Sony device on the **same subnet**. Sony firmware
may reject requests from a different subnet even when routing works. Reserve a
static IP address and turn on the device and its connected TV before pairing.
Enable the device's remote-control/remote-start options and enter its remote
registration screen if required by its firmware.

Enter the device's IPv4 address or hostname **without** `http://`, a port or a path.
Choose a display name and, if necessary, change the three service ports. Leave
the PIN empty (or use `0000`) to request a PIN on the TV. Enter the displayed PIN
in the next step. Devices which do not require a PIN finish registration directly.
A rejected PIN keeps the form open and does not save a broken integration.

| Setting | Default | Purpose |
| --- | --- | --- |
| Host | Required | Device IP address or hostname |
| Name | Sony Device | Device display name in Home Assistant |
| PIN | Empty | Existing PIN, or leave empty to request one |
| App port | 50202 | Application service |
| DMR port | 52323 | Device description and AVTransport service |
| IRCC port | 50001 | IRCC description and remote control service |

Ports vary by model and firmware. Use the ports advertised by the device's UPnP
service descriptions; do not assume every Sony device uses the defaults.
The following override was documented by this project before this update:

| Model | App port | DMR port | IRCC port |
| --- | --- | --- | --- |
| BDP-S590 | 52323 | 50202 | 52323 |

BDV home-theater models, including the BDV-E2100, need their own service ports
verified on the device. This update does not claim hardware validation for them.

Existing version 2.0.0 config entries and entity unique IDs are retained. You do
not need YAML configuration or a `sony.conf` file. If the device revokes its saved
registration, Home Assistant offers a reauthentication flow for the existing entry.

## Controls

The media player exposes playback, track navigation, volume steps and power
controls when the corresponding commands are advertised by the device.
It does not advertise URL playback or absolute volume/mute controls that the
legacy library cannot implement reliably. To toggle mute, send `Mute` through
the remote when it appears in that remote's `commands` attribute.

Both entities share one poll every 60 seconds. Commands request a state refresh.
Playback state comes from AVTransport when available; otherwise the media player
shows `on`. The remote reports `on` for playing, paused and idle devices.
Communication failures show `unavailable`. Scalar API devices with a confirmed
`standby` or `off` power status show `off`; older CERS/IRCC devices may only expose
reachability, so their precise standby state cannot always be determined.

Wake-on-LAN requires the device to advertise a MAC address and support network
standby/remote start. Enable those options if available. A device which fully
shuts down networking may be unavailable and require its physical remote to wake
it, particularly while Home Assistant is starting up.

### Remote commands

Find the actual entity ID and supported command names in **Developer tools >
States**. The remote's `commands` attribute lists the commands returned by your
device; names are case sensitive. Use your actual entity ID in the examples.

```yaml
action: remote.send_command
target:
  entity_id: remote.sony_device_remote
data:
  command: Up
```

Sequences and repetitions are supported:

```yaml
action: remote.send_command
target:
  entity_id: remote.sony_device_remote
data:
  command:
    - Home
    - Down
    - Confirm
  num_repeats: 1
  delay_secs: 0.5
```

Common commands include `Power`, `Play`, `Pause`, `Stop`, `Next`, `Prev`, `Up`,
`Down`, `Left`, `Right`, `Confirm`, `Home`, `Return`, `Options`, `VolumeUp`,
`VolumeDown`, `Mute`, `Eject`, `Display`, `SubTitle`, `Audio` and `Num0`–`Num9`.
Availability depends on the device. Hold/release semantics (`hold_secs`) and
remote activities are not supported.

## Troubleshooting

- **HACS rejects the version:** confirm the download contains the updated
  `hacs.json` and integration manifest. Old commits do not acquire new metadata.
- **Cannot connect:** turn on the Sony device, check the subnet and all three
  ports, and enable remote access. A successful request to one port does not
  confirm the other services work.
- **No PIN appears:** open the device's remote registration screen and retry
  with an empty PIN. Follow the on-screen prompt on the connected TV.
- **Unsupported command:** use the exact name in the remote's `commands`
  attribute. The integration validates the entire sequence before sending it.

To remove the integration, delete its entry in **Settings > Devices & services**,
then remove its download in HACS and restart Home Assistant.

## Development

Use Python 3.14:

```sh
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements_test.txt
ruff check .
ruff format --check .
pytest
```

Tests cover config entries, PIN pairing and reauthentication, state updates,
entity registry compatibility, services, IRCC request headers and error handling.
Sockets are disabled during tests; Sony responses are mocked. CI also runs the
official Hassfest and HACS validation actions.

Maintainers must also set GitHub repository topics (for example `home-assistant`,
`hacs` and `sony`) in the repository's About settings. HACS's repository-level
validation checks these GitHub settings in addition to the files in this checkout.

For optional hardware testing, `bash test_config/create_docker.sh` starts Home
Assistant 2026.10.2 with this checkout mounted as its custom components. It uses
host networking and is intended for a Linux Docker host on the Sony device's
subnet. Configure the device through the UI. Local state is ignored by Git.
