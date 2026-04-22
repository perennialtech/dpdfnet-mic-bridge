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

1. Install VB-CABLE beforehand. This script expects it to already exist, and never installs it for you.
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

3. If no likely virtual mic target is visible and `--output` was omitted, the app tries to create a temporary `DPDFNet_Mic` target automatically with `pactl`. If PortAudio can use it, the bridge runs through it and removes it on exit. If PortAudio still cannot use it immediately, the app stops cleanly and removes the temporary target before handing off.

4. If auto mode still needs help:

   ```text
   python mic_bridge.py list-devices
   python mic_bridge.py run --input 18 --output 16
   ```

   If you want a Linux virtual mic target to keep showing up in `list-devices` between runs, create it manually first.

5. If you prefer a reusable Linux virtual mic target, create it yourself before running the bridge:

   ```text
   pactl load-module module-null-sink media.class=Audio/Source/Virtual sink_name=dpdfnet_mic sink_properties=device.description=DPDFNet_Mic channels=1 channel_map=mono
   ```

   This form uses `media.class=Audio/Source/Virtual` for better app discoverability and `channels=1 channel_map=mono` to avoid channel-mapping surprises.

   A manually created target can keep showing up between runs until you unload it yourself or your audio session resets, unlike the run command's temporary auto-created target.

6. In Discord, OBS, or your app, first try selecting `DPDFNet_Mic` directly as the microphone. If the app does not expose it, try the matching monitor/source instead.

## Runtime attention cues

`python mic_bridge.py run` is often left in the background, so important live events try to raise attention promptly instead of waiting for you to notice the terminal later.

What gets surfaced during a run:

- the first real-time audio glitch reported by PortAudio
- an escalation if those glitches keep happening
- an unexpected stop after streaming has already started

How the alert is surfaced:

- a short attention notice in the terminal
- a terminal bell when attached to a TTY
- on Linux, a best-effort desktop notification via `notify-send` if available
- on Windows, a best-effort system beep via `winsound` if available

The detailed next step still stays in the terminal output. The live alerts are intentionally one-shot and concise so the bridge does not spam you for every single callback status flag.

## Pinned device IDs are intentionally ephemeral

`list-devices` shows the current PortAudio device IDs. Those numeric IDs can change after reboots, hotplug events, backend changes, or audio-session churn.

If a previously working command such as:

```text
python mic_bridge.py run --input 18 --output 16
```

stops working, rerun:

```text
python mic_bridge.py list-devices
```

and pin fresh IDs. The tool intentionally does not try to repair stale IDs by fuzzy-matching device names.

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
