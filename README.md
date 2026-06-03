# mic-bridge

This tool takes your real microphone, runs [DPDFNet](https://github.com/ceva-ip/DPDFNet) enhancement on it, then sends the cleaned audio into a virtual microphone that apps like Discord, OBS, or games can use.

```text
physical mic -> mic-bridge -> virtual mic -> Discord/OBS/game
```

## 1. Install `uv`

Install `uv` first:

<https://docs.astral.sh/uv/getting-started/installation/>

Then open a terminal in the `mic-bridge` project folder and run:

```text
uv sync
```

That installs the project dependencies.

## 2. Download the DPDFNet model

Run this once before the first use:

```text
uv run dpdfnet download dpdfnet8_48khz_hr
```

## 3. Set up your virtual microphone

### On Windows

Install VB-CABLE yourself first:

<https://vb-audio.com/Cable/>

After installing it, you should have endpoints like:

```text
CABLE Input
CABLE Output
```

The bridge writes audio into `CABLE Input`.

Apps should use `CABLE Output` as the microphone.

So in Discord, OBS, etc., choose:

```text
CABLE Output
```

as the mic.

### On Linux with PipeWire

Create the virtual mic target first:

```text
pactl load-module module-null-sink media.class=Audio/Source/Virtual sink_name=dpdfnet_mic sink_properties=device.description=DPDFNet_Mic channels=1 channel_map=mono
```

Then apps should select:

```text
DPDFNet_Mic
```

as the microphone.

Some apps may show the matching source or monitor instead, so pick the one that receives the bridge audio.

## 4. Start the bridge

From the project folder:

```text
uv run mic-bridge
```

Leave that terminal open while using your mic.

Stop it with:

```text
Ctrl+C
```

## 5. Choose the virtual mic in your app

In Discord, OBS, a game, or another app:

On Windows, choose:

```text
CABLE Output
```

On Linux, choose:

```text
DPDFNet_Mic
```

Now your app should hear the enhanced mic audio instead of the raw physical mic.

## When auto device selection gets confused

List devices:

```text
uv run mic-bridge devices
```

It will show device names and IDs.

Then run with a specific input and output:

```text
uv run mic-bridge --input <mic-id> --output <virtual-mic-id>
```

Example shape:

```text
uv run mic-bridge --input 15 --output 19
```

Use your real microphone as `--input`.

Use the virtual target as `--output`, such as `CABLE Input` on Windows or the `DPDFNet_Mic` target on Linux.

Device IDs can change after rebooting, unplugging devices, or audio backend changes, so rerun:

```text
uv run mic-bridge devices
```

when a pinned command stops working.

## The usual full flow

### Windows

```text
uv sync
uv run dpdfnet download dpdfnet8_48khz_hr
uv run mic-bridge
```

Then select `CABLE Output` in Discord/OBS/game.

### Linux PipeWire

```text
uv sync
uv run dpdfnet download dpdfnet8_48khz_hr
pactl load-module module-null-sink media.class=Audio/Source/Virtual sink_name=dpdfnet_mic sink_properties=device.description=DPDFNet_Mic channels=1 channel_map=mono
uv run mic-bridge
```

Then select `DPDFNet_Mic` in Discord/OBS/game.
