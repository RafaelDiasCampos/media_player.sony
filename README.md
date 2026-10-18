# Sony Legacy API

This is a maintained fork of
[dilruacs/media_player.sony](https://github.com/dilruacs/media_player.sony),
the original repository this fork was created from. It updates the legacy Sony
integration for newer Home Assistant releases, including installations running
**Home Assistant OS (HAOS)**, and adds HACS installation support.

Control Sony devices that expose the legacy CERS/IRCC network API from Home
Assistant. The integration creates a media player and a remote for each device.

Requires **Home Assistant Core 2026.10.0 or newer**. On Home Assistant OS, this
means the Home Assistant application version; the operating system has its own
version numbering. Automated tests run against **Home Assistant 2026.10.2 on
Python 3.14**.

**Tested on physical hardware: Sony BDV-E2100.** See the testing details below.

## Tested devices

| Device | Testing confirmed |
| --- | --- |
| **Sony BDV-E2100** | User-confirmed successful PIN pairing and integration setup, working media-player and remote entities, and discovery of **70 advertised remote commands** with version 2.1.1. |

The [BDV-E2100 dashboard example](examples/sony-bdv-e2100-remote.yaml) includes
all 70 commands reported by that device. Each individual button has not been
independently verified; some advertised keys apply only to particular inputs,
regions or related Sony models. Other models and firmware still need testing.

Automated tests use simulated Sony HTTP responses separately from this physical
device report. They cover registration, single-use PINs, setup, reload,
reauthentication, entity behavior and command delivery.

## How the integration works

1. **Discover the device's services.** Home Assistant reads the Sony's UPnP
   descriptions using the configured ports. It follows the advertised CERS/IRCC
   endpoints to read the remote command list and system information. Where
   supported, it also discovers AVTransport playback status and a MAC address
   for Wake-on-LAN.
2. **Pair once.** Devices requiring registration display a PIN on the connected
   TV. Successful pairing saves the registration cookies, where provided, in the
   Home Assistant config entry. Later setup and reload verify access using that
   saved registration and a stable client identity. They do not start another
   registration with an already-consumed PIN. If Sony revokes access, Home
   Assistant asks you to authenticate the existing entry again.
3. **Create two entities.** The `media_player` entity provides the standard
   playback, volume-step and power controls supported by the advertised keys.
   The `remote` entity sends every advertised command through
   `remote.send_command`. Home Assistant's default remote widget shows a power
   toggle; use actions or the dashboard example for the additional buttons.
4. **Keep state updated.** Both entities share one poll every 60 seconds.
   Remote command sequences and media-player playback/power actions also request
   a refresh. AVTransport supplies playback state where available. Legacy devices
   may expose only reachability rather than a reliable standby state.
5. **Control over the local network.** IRCC commands are sent directly to the
   Sony device as HTTP/SOAP requests. Commands and polling are serialized, and
   network operations run outside Home Assistant's event loop. Connection errors
   make the entities unavailable; rejected credentials trigger reauthentication.

Remote commands are **button presses**. For example, `BDV:Function` cycles inputs
and `BDV:SoundModeUp` steps through sound modes. These keys do not provide a
named-input selector, absolute volume setting or feedback about the selected
sound mode. Available keys come from the device rather than a fixed list shared
by every Sony model.

## Install with HACS

1. Open **HACS**, then **⋮ > Custom repositories**.
2. Add `https://github.com/RafaelDiasCampos/media_player.sony` with type **Integration**.
3. Find **Sony Legacy API**, open it and select **Download**.
4. Restart Home Assistant.
5. Open **Settings > Devices & services > Add integration** and search for
   **Sony Legacy API**.

This fork includes the updated code (integration version **2.1.1**) on its
`master` branch. The older
`c264add` revision lacks required HACS metadata and remains incompatible with
HACS. Add this fork's URL to HACS, as shown above.

This repository can be installed as a **custom repository**; inclusion in HACS's
default catalog is a separate submission. Select the latest versioned release
when downloading. If HACS offers only a short commit such as `9c054c0` and reports
an archive 404, use **Update information**, then **Redownload** and select the
latest release or `master` with **Show beta versions** enabled. A short commit
identifier is not a branch name and cannot be downloaded as `refs/heads/<commit>`.

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

The BDV-E2100 has been tested with this fork. Its service ports still need to
match those advertised by the individual device; firmware and models can differ.

Existing version 2.0.0 config entries and entity unique IDs are retained. You do
not need YAML configuration or a `sony.conf` file. If the device revokes its saved
registration, Home Assistant offers a reauthentication flow for the existing entry.
The displayed PIN can be single-use. Successful pairing saves the registration
cookies, where provided, and setup verifies the approved client without starting
registration again. Updating from 2.1.0 may require one new PIN through the
existing entry's **Reconfigure** prompt; restarting then reuses that registration.

## Controls

The media player exposes playback, track navigation, volume steps and power
controls when the corresponding commands are advertised by the device.
It does not advertise URL playback or absolute volume/mute controls that the
legacy library cannot implement reliably. To toggle mute, send `Mute` through
the remote when it appears in that remote's `commands` attribute.

Both entities share one poll every 60 seconds. Remote command sequences and
media-player playback/power actions request a state refresh.
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

The remote's `supported_features: 0` describes optional learning, deletion and
activity features. It does not prevent `remote.send_command` from sending the
commands listed in the entity's `commands` attribute.

### BDV-E2100 dashboard remote

Use [examples/sony-bdv-e2100-remote.yaml](examples/sony-bdv-e2100-remote.yaml)
to add a dashboard remote with all **70 commands** advertised by the tested
BDV-E2100. It uses built-in Home Assistant cards and needs no extra frontend
plugins.

1. Open the example file and copy its complete YAML contents.
2. Find your Sony remote's entity ID under **Developer tools > States**.
3. Replace every `remote.sony_device_remote` in the example if your entity ID
   differs.
4. Open your dashboard, select **Edit > Add card > Manual**, paste the YAML and
   save the card.

This is a **dashboard card**, so paste it into the card editor. The integration
itself is configured through **Devices & services**; the example does not belong
in `configuration.yaml`.

The dashboard groups power, volume, input switching, navigation, playback,
sound settings, color and number keys, browser/app keys and model-specific extras.
Some useful commands are:

| Control | Command |
| --- | --- |
| Cycle inputs | `BDV:Function` |
| Select Bluetooth | `BDV:Bluetooth` |
| Change sound mode | `BDV:SoundMode`, `BDV:SoundModeUp`, `BDV:SoundModeDown` |
| Music equalizer | `BDV:MusicEQ` |
| Football mode | `BDV:FootBall` |
| Sound output | `BDV:SoundOutput` |
| Toggle mute | `Mute` |
| Select audio track or subtitles | `Audio`, `SubTitle` |

Hold and double-tap actions are disabled in the example. Hold/release semantics
are not implemented by this integration. Legacy app buttons such as `Netflix`
and `SEN` may appear even when their network services are no longer available.

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
- **Only a few controls appear:** the standard entity widgets show basic
  controls. Use `remote.send_command` or the dashboard example to access the full
  advertised command list.
- **A listed button has no effect:** check the current input and the device's
  capabilities. The advertised list may contain keys for other operating modes
  or related models, and retired app services cannot be restored by sending a key.

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
