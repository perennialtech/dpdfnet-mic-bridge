# Mic bridge

A command-line DPDFNet microphone bridge for Windows 10/11 and Linux PipeWire setups.

The fast path is intentionally simple:

```text
python mic_bridge.py run
```

Running `python mic_bridge.py` shows top-level help instead of starting the bridge.

Use `python mic_bridge.py run` for the live bridge. That command tries to auto-select a likely microphone input and a safe bridge-style output. If it cannot choose safely, it stops with a short handoff and `list-devices` becomes the fallback discovery workflow.

Run-specific flags belong after the explicit `run` subcommand, for example:

```text
python mic_bridge.py run --input 18 --output 16
```

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
mic_bridge_app/app.py          top-level CLI dispatch and command execution
mic_bridge_app/cli.py          argparse parser and help text
mic_bridge_app/constants.py    static configuration and platform constants
mic_bridge_app/dependencies.py optional import wrappers
mic_bridge_app/domain.py       data models and small stateful helpers
mic_bridge_app/modeling.py     model/sample-rate policy
mic_bridge_app/audio_system.py device probing, host API policy, auto-selection policy, block-size policy
mic_bridge_app/listing.py      fallback discovery and ID-pinning workflow
mic_bridge_app/runtime.py      auto-selection, Linux target creation, model loading, live stream runtime
ARCHITECTURE.md                future-maintainer and future-AI handoff notes
```

If you plan to modify the code, read `ARCHITECTURE.md` before changing behavior.

## Version 0 scope

Version 0 is intentionally small and opinionated.

Included:

- command-line-only workflow
- real-time DPDFNet enhancement via `dpdfnet.StreamEnhancer`
- automatic device selection when it can be done safely
- numeric device ID pinning when auto mode needs help
- explicit `run` command for the live bridge, with `list-devices` as the fallback discovery flow
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
- automatic VB-CABLE installation on Windows

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

You can pre-download the recommended model if you want:

```text
dpdfnet download dpdfnet8_48khz_hr
```

If the selected model is missing when you start the bridge, the run path tries one automatic download and then continues or fails fast.

If that automatic download is interrupted, or if the local model files look partial or inconsistent afterward, rerun `dpdfnet download <model>` manually once and then start the bridge again.

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

   where:
   - `--input` is your real microphone device ID
   - `--output` is the VB-CABLE playback endpoint, usually `CABLE Input`

6. In Discord, OBS, or your game, choose the VB-CABLE recording endpoint, usually `CABLE Output`, as the microphone.

Windows note:

- VB-CABLE must already be installed outside this script.
- Auto mode will not silently pick speakers/headphones or the recording-side endpoint as the bridge output.

## Quick start on Linux with PipeWire

1. Install dependencies.
2. Start the bridge:

   ```text
   python mic_bridge.py run
   ```

3. If no likely virtual mic target is visible and `--output` was omitted, the app tries to create the recommended `DPDFNet_Mic` target automatically with `pactl`.

   If that succeeds, the target is left in place for later runs. If it is created but PortAudio still cannot use it immediately, the app stops cleanly, leaves the target in place, and later runs can reuse it once PortAudio exposes it. If it already exists but PortAudio still cannot use it, the app hands off instead of trying to create duplicate copies.

4. If auto mode still needs help, use fallback discovery:

   ```text
   python mic_bridge.py list-devices
   ```

   Then pin IDs explicitly if needed:

   ```text
   python mic_bridge.py run --input 18 --output 16
   ```

5. If you prefer to create the Linux virtual mic target yourself, use this exact command:

   ```text
   pactl load-module module-null-sink media.class=Audio/Source/Virtual sink_name=dpdfnet_mic sink_properties=device.description=DPDFNet_Mic channels=1 channel_map=mono
   ```

   This exact form is deliberate:
   - `media.class=Audio/Source/Virtual` improves discoverability in many apps
   - `channels=1 channel_map=mono` keeps the target explicitly mono and avoids left/right mapping surprises

6. In Discord, OBS, or your app:
   - first try selecting `DPDFNet_Mic` directly as the microphone
   - if the app does not expose it directly, try the matching monitor/source instead

## Command overview

Fast path, auto-selected when safe:

```text
python mic_bridge.py
# shows help

python mic_bridge.py run
```

Run-specific flags belong after the explicit `run` subcommand.

Fallback device discovery:

```text
python mic_bridge.py list-devices
```

Pinned manual run:

```text
python mic_bridge.py run --input 18 --output 16
```

You can also run the internal package entry point directly:

```text
python -m mic_bridge_app
# shows help

python -m mic_bridge_app run
python -m mic_bridge_app list-devices
python -m mic_bridge_app run --input 18 --output 16
```

## Important behavior notes

- Version 0 intentionally does not do live resampling.
- The stream sample rate must exactly match the model's native sample rate.
- The recommended model `dpdfnet8_48khz_hr` therefore expects 48,000 Hz.
- The bridge uses a single full-duplex `sounddevice.Stream`.
- Input and output must belong to the same PortAudio host API.
- Auto mode is intentionally strict about outputs, it only auto-selects likely bridge-style outputs and will not silently pick speakers/headphones or monitor/source endpoints.
- On Windows, VB-CABLE must already exist before the script is started.
- Ctrl+C during startup or streaming should exit cleanly.
- On Linux, if `--output` is omitted and no likely virtual mic target is visible, the run path tries to create the recommended `DPDFNet_Mic` target automatically with `pactl`.
- If that Linux target is created automatically, it is left in place for later runs rather than being auto-unloaded on exit.
- If the recommended Linux target already exists but PortAudio still cannot use it, the run path hands off instead of trying to create duplicate copies.
- Pinned numeric device IDs are concrete but may change after reboots, driver restarts, or audio-session/device churn. If a previously working ID stops working, rerun `python mic_bridge.py list-devices` and pin fresh IDs.
- On Linux/JACK, omitting `--block-ms` intentionally chooses a friendlier exact frame size than raw 10.0 ms.
- The bridge output is enhanced mono, but output channel negotiation prefers stereo first so Linux virtual sinks do not accidentally become left-only.
- If the chosen model appears to be missing locally, the run path tries one automatic download and then continues or fails fast.
- If the automatic model download is interrupted or still leaves the model unloadable, rerun `dpdfnet download <model>` manually once and then retry.
- `python mic_bridge.py` and `python -m mic_bridge_app` show top-level help when no subcommand is provided.
- Use the explicit `run` subcommand for the live bridge workflow.
- Run-specific flags belong after `run`, for example `python mic_bridge.py run --input 18 --output 16`.
- `list-devices` remains available as the fallback discovery and manual pinning workflow.

## Developer note

This repo intentionally carries design rationale, failure-mode policy, and user-handoff rules forward in `ARCHITECTURE.md` so future humans and future AI sessions do not have to rediscover why Version 0 made certain choices.

If you change auto-selection policy, output strictness, Linux virtual mic creation behavior, model loading flow, stream topology, block sizing, or Linux virtual mic behavior, update `ARCHITECTURE.md` in the same change.
