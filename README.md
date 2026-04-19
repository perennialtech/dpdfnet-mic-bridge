# Mic bridge

A command-line DPDFNet microphone bridge for Windows 10/11 and Linux PipeWire setups. This tool turns a physical microphone into a cleaned virtual microphone by running DPDFNet in real time and writing enhanced audio into a virtual audio endpoint.

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

## Dependencies

The repo `Pipfile` declares the runtime packages:

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

You can pre-download the recommended model:

```text
dpdfnet download dpdfnet8_48khz_hr
```

If the selected model is missing at startup, the run path tries one automatic download. If that download is interrupted or leaves the model inconsistent, rerun `dpdfnet download <model>` manually and then start the bridge again.

## Quick start on Windows

1. Install VB-CABLE.
2. Set your physical microphone and VB-CABLE to 48 kHz in Windows sound settings.
3. Install dependencies.
4. Start the bridge:

   ```text
   python mic_bridge.py run
   ```

5. If auto mode needs help, fall back to discovery and pin the IDs it shows:

   ```text
   python mic_bridge.py list-devices
   python mic_bridge.py run --input 18 --output 16
   ```

   where `--input` is your real microphone device ID and `--output` is the VB-CABLE playback endpoint, usually `CABLE Input`.

6. In Discord, OBS, or your game, choose the VB-CABLE recording endpoint (usually `CABLE Output`) as the microphone.

## Quick start on Linux with PipeWire

1. Install dependencies.
2. Start the bridge:

   ```text
   python mic_bridge.py run
   ```

3. If no likely virtual mic target is visible and `--output` was omitted, the app tries to create the recommended `DPDFNet_Mic` target automatically with `pactl`. If that succeeds, the target is left in place for later runs. If PortAudio still cannot use it immediately, the app stops cleanly and later runs can reuse it once PortAudio exposes it.

4. If auto mode still needs help:

   ```text
   python mic_bridge.py list-devices
   python mic_bridge.py run --input 18 --output 16
   ```

5. If you prefer to create the Linux virtual mic target yourself:

   ```text
   pactl load-module module-null-sink media.class=Audio/Source/Virtual sink_name=dpdfnet_mic sink_properties=device.description=DPDFNet_Mic channels=1 channel_map=mono
   ```

   This form uses `media.class=Audio/Source/Virtual` for better app discoverability and `channels=1 channel_map=mono` to avoid channel-mapping surprises.

6. In Discord, OBS, or your app, first try selecting `DPDFNet_Mic` directly as the microphone. If the app does not expose it, try the matching monitor/source instead.

## Command overview

```text
python mic_bridge.py              # shows top-level help
python mic_bridge.py run          # live bridge with auto-selection
python mic_bridge.py list-devices # fallback discovery workflow
python mic_bridge.py run --input 18 --output 16  # pinned manual run
```

Run-specific flags belong after the explicit `run` subcommand.

You can also use the package entry point directly:

```text
python -m mic_bridge_app run
```
