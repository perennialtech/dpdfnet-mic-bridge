# Mic bridge

Small command-line DPDFNet microphone bridge for Windows and Linux PipeWire. It reads a real microphone, enhances it in real time, and writes the result to a virtual microphone target.

Typical flow:

```text
physical mic -> mic_bridge -> virtual mic endpoint -> Discord/OBS/game
```

## Install

```text
pip install numpy sounddevice dpdfnet
```

Or with Pipenv:

```text
pipenv install
```

Download the model before first run if needed:

```text
dpdfnet download dpdfnet8_48khz_hr
```

## Start

```text
python mic_bridge.py
```

Stop with Ctrl+C.

If auto-selection needs help:

```text
python mic_bridge.py devices
python mic_bridge.py --input 15 --output 19
```

`devices` marks what auto mode may choose with `auto`; everything else is `pin-only`.

`devices` prints device names first and IDs next to them. IDs can change after reboots, hotplug events, backend changes, or audio-session churn; rerun `devices` when a pinned command stops working.

## Windows

Install VB-CABLE yourself first. This tool does not install it.

Use:

```text
python mic_bridge.py
```

In apps, choose VB-CABLE's recording endpoint, usually `CABLE Output`, as the microphone. The bridge writes to the playback endpoint, usually `CABLE Input`.

If automatic selection fails:

```text
python mic_bridge.py devices
python mic_bridge.py --input <mic-id> --output <cable-input-id>
```

## Linux PipeWire

Create a virtual microphone target first:

```text
pactl load-module module-null-sink media.class=Audio/Source/Virtual sink_name=dpdfnet_mic sink_properties=device.description=DPDFNet_Mic channels=1 channel_map=mono
```

Then run:

```text
python mic_bridge.py
```

In apps, select `DPDFNet_Mic` as the microphone. If the app does not show it, try its matching source/monitor.

If automatic selection fails:

```text
python mic_bridge.py devices
python mic_bridge.py --input <mic-id> --output <dpdfnet-mic-id>
```

## Glitches and block size

If the audio backend reports overflows/underflows, the bridge prints a concrete restart command.

For JACK/PipeWire-JACK, exact frame counts are usually the clearest knob:

```text
python mic_bridge.py --block-frames 2048
```

For other backends, milliseconds are also available:

```text
python mic_bridge.py --block-ms 30
```

## Runtime warnings

The callback records PortAudio status warnings without printing from the real-time thread. On shutdown, the bridge prints a compact warning summary and a concrete restart command with a larger block size if needed.

## Commands

```text
python mic_bridge.py                         # start bridge
python mic_bridge.py run                     # same as above
python mic_bridge.py devices                 # list current devices
python mic_bridge.py --input 15 --output 19  # pin IDs
python mic_bridge.py devices --all-apis
python mic_bridge.py --block-frames 2048     # exact JACK-friendly block size
python -m mic_bridge_app
```

Version 0 uses the selected model's native sample rate. There is no live resampling and no sample-rate override flag.
