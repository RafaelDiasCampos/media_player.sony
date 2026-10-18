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
| **Sony BDV-E2100** | User-confirmed successful PIN pairing and integration setup, working media-player and remote entities, and discovery of **70 advertised remote commands** with version 2.1.1. A direct RenderingControl query also confirmed volume readback while reproducing TV audio. |

The [BDV-E2100 dashboard example](examples/sony-bdv-e2100-remote.yaml) includes
all 70 commands reported by that device. Each individual button has not been
independently verified; some advertised keys apply only to particular inputs,
regions or related Sony models. Other models and firmware still need testing.

The BDV-E2100 advertises `GetVolume`, `SetVolume`, `GetMute` and `SetMute` on a
0–100 volume scale. The confirmed physical-device query returned volume `73`
while using TV audio with no disc loaded. Absolute volume setting and mute are
covered by simulated HTTP tests; they still need confirmation on physical hardware.

Automated tests use simulated Sony HTTP responses separately from this physical
device report. They cover registration, single-use PINs, setup, reload,
reauthentication, entity behavior and command delivery.

## How the integration works

1. **Discover the device's services.** Home Assistant reads the Sony's UPnP
   descriptions using discovered URLs or manually configured ports. It follows
   the advertised CERS/IRCC endpoints to read the remote command list and system
   information. Where supported, it also discovers AVTransport playback status, RenderingControl
   audio capabilities and a MAC address for Wake-on-LAN. Audio support follows
   the advertised service/control URLs, action names and volume range.
2. **Pair once.** Devices requiring registration display a PIN on the connected
   TV. Successful pairing saves the registration cookies, where provided, in the
   Home Assistant config entry. Later setup and reload verify access using that
   saved registration and a stable client identity. They do not start another
   registration with an already-consumed PIN. If Sony revokes access, Home
   Assistant asks you to authenticate the existing entry again.
3. **Create two entities.** The `media_player` entity provides the standard
   playback, volume-step and power controls supported by the advertised keys.
   Devices with usable RenderingControl audio queries also report volume and
   mute state, with an absolute-volume slider and mute control when the matching
   setters are advertised.
   The `remote` entity sends every advertised command through
   `remote.send_command`. Home Assistant's default remote widget shows a power
   toggle; use actions or the dashboard example for the additional buttons.
4. **Keep state updated.** Both entities share one poll every 60 seconds.
   Remote command sequences and media-player playback, power and volume actions
   also request a refresh. AVTransport supplies playback state where available.
   RenderingControl supplies volume and mute readings independently; a failed
   optional audio query leaves that value unknown while the remote remains usable.
   Legacy devices may expose only reachability rather than a reliable standby state.
5. **Control over the local network.** IRCC commands are sent directly to the
   Sony device as HTTP/SOAP requests. Commands and polling are serialized, and
   network operations run outside Home Assistant's event loop. Connection errors
   on the main connection leave playback state unknown when a saved MAC allows
   Wake-on-LAN, or make the entities unavailable otherwise. Rejected CERS
   credentials trigger reauthentication. DLNA audio-access failures are handled
   separately.

Remote commands are **button presses**. For example, `BDV:Function` cycles inputs
and `BDV:SoundModeUp` steps through sound modes. These keys do not provide a
named-input selector or feedback about the selected sound mode. Absolute volume
and mute setters use the separate RenderingControl service when supported.
Available keys come from the device rather than a fixed list shared by every
Sony model.

## Volume, mute and current mode

Version **2.3.0** adds volume and mute support through UPnP RenderingControl.
The media player's `volume_level` attribute uses Home Assistant's **0–1** scale;
for the BDV-E2100, a device reading of `73` becomes `0.73` (73% in the UI).
This percentage describes the advertised network volume scale. It is not a dB
measurement or a claim about the number shown on the front panel.

Use the volume slider or standard Home Assistant actions. Replace the entity ID
with the one shown in **Developer tools > States**:

```yaml
action: media_player.volume_set
target:
  entity_id: media_player.sony_device
data:
  volume_level: 0.3
```

```yaml
action: media_player.volume_mute
target:
  entity_id: media_player.sony_device
data:
  is_volume_muted: true
```

The integration reads `GetVolume` and `GetMute`, and uses `SetVolume` and
`SetMute` for explicit changes. Mute is not emulated by sending a toggle button.
Each setter is offered only when its action is advertised and the related
readback succeeds. Failed or invalid readings become unknown and are retried on
the next poll. Devices without these services retain their existing controls.
Changes from a physical remote are picked up by the shared 60-second poll;
actions sent through this integration request an immediate refresh.

**Current input and sound mode are not reported yet.** On the tested BDV-E2100,
the observed CERS `getStatus` response contained only `party.status=idle`;
this is the PARTY streaming status. `getContentInformation` returned
`DI01 NoDisc`, and RenderingControl listed only the `FactoryDefaults` preset.
These responses do not establish an API for reading the selected TV/Bluetooth
input or sound mode. The integration does not infer either value from button presses.

## Automatic discovery and address updates

Version **2.2.0** adds automatic discovery through Home Assistant's shared
**SSDP/UPnP** service. Turn on the Sony device and connect it to the same local
network as Home Assistant. Compatible devices appear under **Settings > Devices
& services > Discovered**. Select **Configure**, confirm the device, and complete
PIN pairing if requested. Discovery uses the advertised description paths and
ports, including devices with separate IRCC and media-renderer descriptions.

For a configured device, the integration matches its **MAC address** when Home
Assistant receives DHCP discovery information. SSDP discoveries also match the
Sony's observed MAC address or stable **UPnP device ID (UDN)**. When a matching
device announces a different IP, the saved address is updated and the connection
reloads automatically, reusing the existing registration.

Home Assistant learns and saves these hardware identifiers when it connects to
the Sony. New entries use a hardware-based identity where available. Matching
discovery events update an existing entry instead of adding a duplicate.

Discovery depends on local network visibility: SSDP multicast and the relevant
DHCP discovery information must reach Home Assistant. The standard Home
Assistant configuration includes SSDP and DHCP. An address change is handled
when matching discovery information arrives; it is not detected by scanning all
IP addresses. A device with its network interface fully asleep may not advertise
until it wakes.

Automatic discovery and IP-change recovery are covered by simulated Sony network
tests. Physical BDV-E2100 testing so far covers the pairing/setup, command
discovery and volume query described above.

## Install with HACS

1. Open **HACS**, then **⋮ > Custom repositories**.
2. Add `https://github.com/RafaelDiasCampos/media_player.sony` with type **Integration**.
3. Find **Sony Legacy API**, open it and select **Download**.
4. Restart Home Assistant.
5. Open **Settings > Devices & services**. Configure a discovered Sony device, or
   select **Add integration** and search for **Sony Legacy API** to enter its
   address manually.

This fork includes the updated code (integration version **2.3.0**) on its
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
may reject requests from a different subnet even when routing works. Turn on
the device and its connected TV before pairing.
Enable the device's remote-control/remote-start options and enter its remote
registration screen if required by its firmware.

For manual setup, enter the device's IPv4 address or hostname **without**
`http://`, a port or a path.
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

## Controls

The media player exposes playback, track navigation, volume steps and power
controls when the corresponding commands are advertised by the device.
RenderingControl adds volume readback, an absolute-volume slider, mute status
and explicit mute control when supported. URL playback is not implemented.
On devices without a mute setter, the remote's advertised `Mute` command is
still available as a toggle.

Both entities share one poll every 60 seconds. Remote command sequences and
media-player playback, power and volume actions request a state refresh.
Playback state comes from AVTransport when available; otherwise the media player
shows `on`. The remote reports `on` for playing, paused and idle devices.
On a communication failure, a device with a known MAC shows `unknown` and keeps
its power-on action available; other media controls are hidden until it responds.
A device without a usable MAC shows `unavailable`. Scalar API devices with a
confirmed `standby` or `off` power status show `off`; older CERS/IRCC devices may only expose
reachability, so their precise standby state cannot always be determined.

### Turn on with Wake-on-LAN

The integration learns and saves the MAC advertised by Sony's WOL function.
After initial setup while the Sony is awake, use either `media_player.turn_on`
or `remote.turn_on` to send a Wake-on-LAN magic packet to that MAC. The packet
uses the local broadcast address `255.255.255.255` and UDP port `9`, so it does
not depend on the Sony's current IP address.

On the **BDV-E2100**, enable **Setup > Network Settings > Remote Start > On**.
Sony's [operating instructions](https://retronik.silicium.org/DOCUMENTS/Audiovideo/Sony/Sony-BDV-E2100-Owners-Manual.pdf)
(page 51) describe this setting as enabling network power-on from standby.
Home Assistant and the device must have a network path that permits the broadcast.

```yaml
action: media_player.turn_on
target:
  entity_id: media_player.sony_device
```

Power-on stays available when the device stops answering HTTP, including when
Home Assistant restarts while an already configured Sony is asleep. Its state
remains `unknown` until communication succeeds. After sending WOL, the integration
checks for recovery every five seconds, up to six times, then continues normal
60-second polling. These checks run in the background. A wake request never
follows the magic packet with a potentially conflicting `Power` toggle.

Devices without a known MAC retain the existing IRCC power behavior. Wake-packet
generation, startup while asleep and recovery are covered by simulated tests;
physical BDV-E2100 wake-up still needs confirmation with Remote Start enabled.

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

The dashboard includes separate turn-on/turn-off actions and all 70 advertised
remote commands. It groups power, volume, input switching, navigation, playback,
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
- **Discovery does not appear:** turn on the device and check that its SSDP
  multicast advertisements can reach Home Assistant on the same subnet. Routers,
  VLANs, guest Wi-Fi and wireless client isolation can block discovery traffic.
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
Audio tests also cover the required SOAP encoding, volume scaling and steps,
mute setters, discovered service URLs, optional query failures and recovery.
Power tests check the WOL packet, offline startup, background boot checks and
cancellation when the integration unloads.
Sockets are disabled during tests; Sony responses are mocked. CI also runs the
official Hassfest and HACS validation actions.

Maintainers must also set GitHub repository topics (for example `home-assistant`,
`hacs` and `sony`) in the repository's About settings. HACS's repository-level
validation checks these GitHub settings in addition to the files in this checkout.

For optional hardware testing, `bash test_config/create_docker.sh` starts Home
Assistant 2026.10.2 with this checkout mounted as its custom components. It uses
host networking and is intended for a Linux Docker host on the Sony device's
subnet. Configure the device through the UI. Local state is ignored by Git.
