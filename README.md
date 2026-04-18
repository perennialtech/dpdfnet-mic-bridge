# Mic bridge

A command-line DPDFNet microphone bridge for Windows 10/11 and Linux PipeWire setups.

This tool turns a physical microphone into a cleaned virtual microphone by running DPDFNet in real time and writing the enhanced audio into a virtual audio endpoint.

Typical signal flows:

Windows:

```text
physical mic -> this script -> VB-CABLE playback endpoint ("CABLE Input")
chat/game/OBS app -> VB-CABLE recording endpoint ("CABLE Output")
```

Linux with PipeWire:

```text
physical mic -> this script -> PipeWire virtual mic target created via module-null-sink
chat/game/OBS app -> that virtual mic target, or its matching monitor/source depending on the app stack
```

## Project layout

The top-level `mic_bridge.py` file is intentionally just a thin launcher.

The real implementation lives in `mic_bridge_app/`:

```text
mic_bridge.py                  thin entry point
mic_bridge_app/app.py          top-level CLI dispatch and error handling
mic_bridge_app/cli.py          argparse parser and help text
mic_bridge_app/constants.py    static configuration and platform constants
mic_bridge_app/dependencies.py optional import wrappers
mic_bridge_app/domain.py       data models and small callback-safe helpers
mic_bridge_app/modeling.py     model/sample-rate policy
mic_bridge_app/audio_system.py device probing, host API policy, block-size policy
mic_bridge_app/listing.py      list-devices workflow
mic_bridge_app/runtime.py      run workflow and live audio callback
ARCHITECTURE.md                future-maintainer and future-AI handoff notes
```

If you plan to modify the code, read `ARCHITECTURE.md` before changing behavior.

## Version 0 scope

Version 0 is intentionally small and opinionated.

Included:

- command-line-only workflow
- real-time DPDFNet enhancement via `dpdfnet.StreamEnhancer`
- numeric device ID selection
- recommended first-run device listing and probing
- Windows and Linux/PipeWire-oriented behavior

Explicit non-goals:

- GUI
- AGC
- monitoring path
- model hot-switching
- device hotplug recovery
- separate `validate` subcommand
- timed run mode
- native PipeWire graph management
- packaging into an `.exe`

## Dependencies

The repo `Pipfile` declares the runtime packages used by the code:

- `numpy`
- `sounddevice`
- `dpdfnet`

Using Pipenv:

```text
pipenv install
```

Or with plain `pip`:

```text
pip install numpy sounddevice dpdfnet
```

You must also download the DPDFNet model you want to use. The recommended model is:

```text
dpdfnet8_48khz_hr
```

Download it once:

```text
dpdfnet download dpdfnet8_48khz_hr
```

## Quick start on Windows

1. Install VB-CABLE.
2. Set your physical microphone and VB-CABLE to 48 kHz in Windows sound settings.
3. Install dependencies.
4. Download the model:
   ```text
   dpdfnet download dpdfnet8_48khz_hr
   ```
5. List recommended devices:
   ```text
   python mic_bridge.py list-devices
   ```
6. Start the bridge:
   ```text
   python mic_bridge.py run --input 18 --output 16
   ```
   where:
   - `--input` is your real microphone device ID
   - `--output` is the VB-CABLE playback endpoint, usually `CABLE Input`
7. In Discord, OBS, or your game, choose the VB-CABLE recording endpoint, usually `CABLE Output`, as the microphone.

## Quick start on Linux with PipeWire

1. Create a virtual mic target:

   ```text
   pactl load-module module-null-sink media.class=Audio/Source/Virtual sink_name=dpdfnet_mic sink_properties=device.description=DPDFNet_Mic channels=1 channel_map=mono
   ```

   This exact form is deliberate:
   - `media.class=Audio/Source/Virtual` improves discoverability in many apps
   - `channels=1 channel_map=mono` keeps the target explicitly mono and avoids left/right mapping surprises

2. Install dependencies.
3. Download the model:
   ```text
   dpdfnet download dpdfnet8_48khz_hr
   ```
4. List recommended devices:
   ```text
   python mic_bridge.py list-devices
   ```
5. Start the bridge:

   ```text
   python mic_bridge.py run --input 18 --output 16
   ```

   where:
   - `--input` is your physical microphone device ID
   - `--output` is the virtual mic target playback/output device ID

6. In Discord, OBS, or your app:
   - first try selecting `DPDFNet_Mic` directly as the microphone
   - if the app does not expose it directly, try the matching monitor/source instead

## Command overview

List devices:

```text
python mic_bridge.py list-devices
```

Run the bridge:

```text
python mic_bridge.py run --input 18 --output 16
```

You can also run the internal package entry point directly:

```text
python -m mic_bridge_app list-devices
python -m mic_bridge_app run --input 18 --output 16
```

## Important behavior notes

- Version 0 intentionally does not do live resampling.
- The stream sample rate must exactly match the model's native sample rate.
- The recommended model `dpdfnet8_48khz_hr` therefore expects 48,000 Hz.
- The bridge uses a single full-duplex `sounddevice.Stream`.
- Input and output must belong to the same PortAudio host API.
- On Linux/JACK, omitting `--block-ms` intentionally chooses a friendlier exact frame size than raw 10.0 ms.
- The bridge output is enhanced mono, but output channel negotiation prefers stereo first so Linux virtual sinks do not accidentally become left-only.

## Developer note

This repo intentionally carries design rationale forward in `ARCHITECTURE.md` so future humans and future AI sessions do not have to rediscover why Version 0 made certain choices.

If you change stream topology, block sizing, device negotiation, or Linux virtual mic behavior, update `ARCHITECTURE.md` in the same change.
