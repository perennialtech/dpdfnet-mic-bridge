# Mic bridge architecture

This document is the handoff memo for future maintainers and future AI coding sessions.

Read this before changing behavior.

## Purpose of the split

The code is split by responsibility so the important policy boundaries stay easy to find and easy to audit.

Those boundaries are:

- entry-point behavior
- parser construction
- dependency loading
- model/sample-rate policy
- sounddevice and PortAudio host-API policy
- device probing and scoring
- live runtime configuration
- the callback itself
- long-form architectural rationale

The goal is not maximal modularity. The goal is to keep each kind of decision in one obvious home.

Avoid two failure modes:

- collapsing everything back into one large file
- over-correcting into helper-per-concept code and tiny abstraction churn

Prefer cohesive modules and direct code.

## File map

```text
mic_bridge.py
    Thin launcher. Keep it boring.

mic_bridge_app/__init__.py
mic_bridge_app/__main__.py
    Package entry points.

mic_bridge_app/app.py
    Top-level command dispatch and top-level error formatting.

mic_bridge_app/cli.py
    argparse parser creation only.
    If you add or rename flags, start here.

mic_bridge_app/constants.py
    Static configuration values and policy constants.
    No runtime logic should live here.

mic_bridge_app/dependencies.py
    Optional imports and user-friendly "please install X" errors.

mic_bridge_app/domain.py
    Dataclasses and tiny stateful helpers used by the callback flow:
    - DeviceProbe
    - RunConfig
    - AudioFifo
    - CallbackStatusCounter

mic_bridge_app/modeling.py
    Model-name to native-sample-rate policy.
    This is where the "Version 0 does not do live resampling" rule is enforced.

mic_bridge_app/audio_system.py
    PortAudio and sounddevice behavior:
    - host API normalization and filtering
    - host API preference scoring
    - device tags
    - channel negotiation
    - device probing
    - scoring and run-pair suggestion
    - block-size policy, including Linux/JACK defaults
    - Linux virtual mic helper command

mic_bridge_app/listing.py
    The list-devices command, including platform-specific usage notes.

mic_bridge_app/runtime.py
    The run command:
    - build RunConfig
    - print run summary
    - load StreamEnhancer
    - open the duplex stream
    - run the callback
    - print shutdown status summary
```

## Code style guardrails

These are intentionally explicit because future cleanup passes, including AI-driven ones, often make this kind of code worse by trying to make it look more abstract than it needs to be.

### Keep policy centralized

If a rule is platform-specific, device-selection-specific, or model-specific, keep that rule in the module that already owns that policy.

Examples:

- sample-rate compatibility belongs in `modeling.py`
- host-API choice and probing rules belong in `audio_system.py`
- parser text belongs in `cli.py`
- callback behavior belongs in `runtime.py`

Do not scatter one policy across several files just to make individual functions shorter.

### Prefer direct code over helper sprawl

A small amount of repetition is acceptable when it keeps logic readable in place.

Bad refactors for this repo usually look like:

- moving obvious one-line operations into a separate helper
- introducing a utility module for formatting that is only used once or twice
- splitting a simple linear workflow into many tiny functions that obscure control flow

Good refactors are the opposite:

- remove drift-prone duplication
- tighten correctness where several places must agree
- keep the main flow easy to read top-to-bottom

### Keep user-facing docs synchronized

If behavior changes, update all relevant documentation in the same change.

That usually includes:

- the touched module docstrings
- `README.md` for user-facing behavior
- this file for architectural rules or workflow changes

Documentation is part of the design, not optional polish.

## Core Version 0 invariants

These are the important architectural facts that should not be casually changed.

### Use `dpdfnet.StreamEnhancer`, not raw ONNX inference

This is deliberate.

The model is not a simple waveform-in, waveform-out black box. Real-time use requires surrounding state, causal STFT, overlap-add ISTFT, and buffering. `StreamEnhancer` is the correct abstraction boundary for Version 0.

If someone proposes direct ONNX calls inside the callback, treat that as a different design, not a cleanup.

### No live resampling

The stream sample rate must exactly match the model's native sample rate.

For the recommended model `dpdfnet8_48khz_hr`, that means 48,000 Hz.

This policy is enforced in `modeling.py`.

### Keep a single full-duplex `sounddevice.Stream`

This is intentional.

It avoids reintroducing manual queueing, drift, and clock-domain problems that come with separate input and output streams.

If someone wants separate streams later, that is a new architecture, not a small refactor.

### Keep the output FIFO

Do not remove `AudioFifo` just because the common case looks one-in, one-out.

`StreamEnhancer.process()` may legally return fewer or more samples than one callback block, especially around startup or with non-hop-aligned block sizes. The FIFO protects against dropped or duplicated audio.

### Keep same-host-API enforcement

Input and output must belong to the same PortAudio host API.

That rule exists to reduce needless backend mismatch pain.

Recommended choices:

- Windows: WASAPI
- Linux on PipeWire desktops: PipeWire if exposed by the PortAudio build, otherwise PulseAudio, then JACK, then ALSA

### Device selection remains numeric IDs

Version 0 intentionally uses numeric IDs rather than fuzzy name lookup.

That keeps the behavior explicit and easier to debug.

`list-devices` may print hints and a likely first-run command, but actual selection remains ID-based.

### `list-devices` is the discovery workflow

There is intentionally no separate `validate` subcommand.

The intended flow is:

1. `list-devices`
2. choose device IDs
3. `run`
4. stop with Ctrl+C after a quick smoke test

If a future change adds `validate`, treat that as an intentional CLI expansion.

### Linux/JACK block sizing is intentionally special

If the host API is JACK on Linux and the user omits `--block-ms`, the code does not use a literal 10.0 ms default.

Instead it chooses a roomier exact frame size, currently 1024 frames at 48 kHz, which is 21.333 ms.

Why:

- raw 10.0 ms at 48 kHz becomes 480 frames
- Python + PortAudio + JACK often behaves better with exact JACK-friendly frame counts
- this change was made after real-world stutter reports

If someone proposes removing that policy, the burden of proof should include real glitch testing.

### Output channel negotiation prefers stereo first

This is for output only.

Input negotiation still prefers mono first because that matches speech capture naturally.

Output negotiation prefers 2 channels, then 1, then max channels because opening some Linux virtual sinks as mono can lead to left-only playback. The bridge signal itself is still enhanced mono, duplicated across all opened output channels.

Do not "simplify" this back to mono-first for output.

### The Linux virtual mic command is intentionally specific

The recommended command is:

```text
pactl load-module module-null-sink media.class=Audio/Source/Virtual sink_name=dpdfnet_mic sink_properties=device.description=DPDFNet_Mic channels=1 channel_map=mono
```

This is not an arbitrary shell snippet.

It intentionally uses:

- `media.class=Audio/Source/Virtual` to make many apps expose it more naturally as a microphone-like target
- `channels=1 channel_map=mono` to keep the node explicitly mono and avoid channel-mapping surprises

Even though the bridge writes to it as an output/playback device from PortAudio's point of view, the mic-like presentation in desktop apps is useful.

## Command flow

### `list-devices`

`list-devices` does four things:

1. resolves the model-native sample rate
2. chooses a platform-friendly default host API filter
3. probes devices for openability at that sample rate
4. prints a likely first-run command when a good pair is obvious

It is intentionally optimized for first-run usability instead of being a raw device dump.

### `run`

`run` does five things:

1. validates the selected devices and host APIs
2. resolves effective block size and negotiated channel counts
3. prints a summary so the user can spot mistakes before audio starts
4. opens one duplex stream and runs the callback
5. prints callback underflow/overflow summary on shutdown

## Where to edit for common changes

If you want to change parser flags or help text, edit `mic_bridge_app/cli.py`.

If you want to change host-API defaults, device tagging, channel probing, device scoring, or Linux/JACK block policy, edit `mic_bridge_app/audio_system.py`.

If you want to change what `list-devices` prints, edit `mic_bridge_app/listing.py`.

If you want to change stream setup, callback logic, or run summaries, edit `mic_bridge_app/runtime.py`.

If you want to add new shared structures, edit `mic_bridge_app/domain.py`.

If you add a new model or change native sample-rate policy, edit `mic_bridge_app/constants.py` and `mic_bridge_app/modeling.py`.

## Things Version 0 intentionally does not do

- GUI
- AGC
- plugin hosting
- live model hot-switching
- device hotplug recovery
- background service behavior
- timed runs
- native PipeWire graph management
- separate validate command
- executable packaging

## Guidance for future AI sessions

If you are an AI assistant editing this repo:

- do not collapse the code back into one file
- do not replace straightforward code with many tiny wrappers
- keep architecture rules and user docs aligned with the code
- treat stream topology, sample-rate policy, and host-API policy as deliberate design choices unless the task explicitly asks to redesign them

If you make behavior changes, update the docs in the same patch.
