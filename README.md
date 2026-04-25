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

Optional pre-download:

```text
dpdfnet download dpdfnet8_48khz_hr
```

If the model is missing, startup tries one automatic download.

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

Use:

```text
python mic_bridge.py
```

If no usable bridge output exists, the bridge tries to create a temporary `DPDFNet_Mic` target, waits briefly for PortAudio/JACK/PipeWire to expose it, uses it, and removes it on exit.

In apps, first select `DPDFNet_Mic` as the microphone. If the app does not show it, try its matching monitor/source.

For a reusable target instead of the temporary one:

```text
pactl load-module module-null-sink media.class=Audio/Source/Virtual sink_name=dpdfnet_mic sink_properties=device.description=DPDFNet_Mic channels=1 channel_map=mono
```

If automatic selection fails:

```text
python mic_bridge.py devices
python mic_bridge.py --input <mic-id> --output <dpdfnet-mic-id>
```

## Runtime attention cues

Important live events print a short terminal notice. The bridge may also use a terminal bell, Linux `notify-send`, or Windows `winsound` when available.

The callback itself never prints or launches notifications; alerts are queued and emitted from the main thread.

## Commands

```text
python mic_bridge.py                         # start bridge
python mic_bridge.py run                     # same as above
python mic_bridge.py devices                 # list current devices
python mic_bridge.py --input 15 --output 19  # pin IDs
python mic_bridge.py devices --all-apis
python -m mic_bridge_app
```

Version 0 uses the selected model's native sample rate. There is no live resampling and no sample-rate override flag.
