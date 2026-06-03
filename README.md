# mic-bridge

Small command-line DPDFNet microphone bridge for Windows and Linux PipeWire. It reads a real microphone, enhances it in real time, and writes the result to a virtual microphone target.

Typical flow:

```text
physical mic -> mic-bridge -> virtual mic endpoint -> Discord/OBS/game
```

## Install

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then from this project directory run:

```text
uv sync
```

Download the model before first run if needed:

```text
uv run dpdfnet download dpdfnet8_48khz_hr
```

## Start

```text
uv run mic-bridge
```

Stop with Ctrl+C.

If auto-selection needs help:

```text
uv run mic-bridge devices
uv run mic-bridge --input 15 --output 19
```

`devices` marks what auto mode may choose with `auto`; everything else is `pin-only`.

`devices` prints device names first and IDs next to them. IDs can change after reboots, hotplug events, backend changes, or audio-session churn; rerun `devices` when a pinned command stops working.

## Windows

Install [VB-CABLE](https://vb-audio.com/Cable/) yourself first. This tool does not install it.

Use:

```text
uv run mic-bridge
```

In apps, choose VB-CABLE's recording endpoint, usually `CABLE Output`, as the microphone. The bridge writes to the playback endpoint, usually `CABLE Input`.

If automatic selection fails:

```text
uv run mic-bridge devices
uv run mic-bridge --input <mic-id> --output <cable-input-id>
```

## Linux PipeWire

Create a virtual microphone target first:

```text
pactl load-module module-null-sink media.class=Audio/Source/Virtual sink_name=dpdfnet_mic sink_properties=device.description=DPDFNet_Mic channels=1 channel_map=mono
```

Then run:

```text
uv run mic-bridge
```

In apps, select `DPDFNet_Mic` as the microphone. If the app does not show it, try its matching source/monitor.

If automatic selection fails:

```text
uv run mic-bridge devices
uv run mic-bridge --input <mic-id> --output <dpdfnet-mic-id>
```
