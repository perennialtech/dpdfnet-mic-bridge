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
- device probing, scoring, and auto-selection candidate policy
- live runtime configuration and smooth user handoffs
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
    Top-level command dispatch, no-subcommand convenience behavior,
    and top-level error formatting.

mic_bridge_app/cli.py
    argparse parser creation only.
    If you add or rename flags, start here.

mic_bridge_app/constants.py
    Static configuration values and policy constants.
    No runtime logic should live here.

mic_bridge_app/dependencies.py
    Optional imports and user-friendly "please install X" errors.

mic_bridge_app/domain.py
    Dataclasses and tiny stateful helpers used by device resolution and the
    callback flow:
    - DeviceProbe
    - DeviceSelection
    - RunConfig
    - AudioFifo
    - CallbackStatusCounter

mic_bridge_app/modeling.py
    Model-name to native-sample-rate policy.
    This is where the "Version 0 does not do live resampling" rule is enforced.

mic_bridge_app/audio_system.py
    PortAudio and sounddevice behavior:
    - audio inventory querying and concise enumeration-failure handoffs
    - host API normalization and filtering
    - host API preference scoring
    - device tags
    - bridge-output identification policy
    - channel negotiation
    - device probing
    - scoring and run-pair suggestion
    - auto-selection candidate policy
    - block-size policy, including Linux/JACK defaults
    - Linux virtual mic helper command

mic_bridge_app/listing.py
    The list-devices command, used as the fallback discovery and pinning workflow.

mic_bridge_app/runtime.py
    The run command:
    - resolve explicit and auto-selected devices
    - print short handoffs when auto mode cannot choose safely
    - optionally create the recommended Linux virtual mic target
    - build RunConfig
    - print run summary
    - load StreamEnhancer
    - do a one-shot automatic model download when the model looks missing
    - open the duplex stream
    - keep interrupted/failed-run handoffs concise around persistent Linux target state
    - run the callback
    - print shutdown status summary
```

## Code style guardrails

These are intentionally explicit because future cleanup passes, including AI-driven ones, often make this kind of code worse by trying to make it look more abstract than it needs to be.

### Keep policy centralized

If a rule is platform-specific, device-selection-specific, or model-specific, keep that rule in the module that already owns that policy.

Examples:

- sample-rate compatibility belongs in `modeling.py`
- host-API choice, device tagging, bridge-output identification, probing rules, scoring, and auto-selection heuristics belong in `audio_system.py`
- parser text belongs in `cli.py`
- automatic Linux virtual mic creation, one-shot model download, and callback behavior belong in `runtime.py`

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

The FIFO also keeps chunk storage plus a read offset instead of rebuilding partially consumed chunks into new arrays. Keep that low-churn behavior in the callback path.

### Keep same-host-API enforcement

Input and output must belong to the same PortAudio host API.

That rule exists to reduce needless backend mismatch pain.

Recommended choices:

- Windows: WASAPI
- Linux on PipeWire desktops: PipeWire if exposed by the PortAudio build, otherwise PulseAudio, then JACK, then ALSA

### Default invocation shows top-level help

This is deliberate UX policy.

Invoking `python mic_bridge.py` or `python -m mic_bridge_app` without a subcommand should show the top-level help text instead of starting the live run path implicitly.

The explicit workflows remain `run` and `list-devices`, and `--help` should continue to show the same top-level help intentionally.

### Auto mode is convenience, not fuzzy chaos

Auto mode is allowed to select devices, but only under explicit safety rules.

Important consequences:

- auto-selected outputs must look like bridge outputs
- auto-selected outputs must be playback-side bridge targets, not monitor/source endpoints
- auto mode must not silently route enhanced mic audio into speakers or headphones
- if auto mode cannot choose safely, it should stop and give a short, actionable handoff rather than guessing

Manual numeric IDs remain the explicit fallback.

### Interrupted and failed runs should hand off cleanly

Version 0 is intentionally strict about cleanup and handoffs:

- Ctrl+C during setup should exit cleanly without a traceback dump
- failed stream opens should stop with a short, actionable handoff
- repeated runs should not rely on hidden background state repair
- if a Linux virtual mic target was created, later runs should either reuse it or explain clearly why PortAudio still cannot use it
- do not add retry loops, auto-healing daemons, or broad cleanup logic just to hide environment problems

### Manual IDs remain numeric

Version 0 still uses numeric IDs for the explicit path.

That keeps manual behavior concrete and easy to debug.

Auto mode may choose IDs internally, but when the user wants to pin a selection, the interface remains ID-based rather than fuzzy name lookup.

### Windows still requires VB-CABLE to be installed outside the script

Do not teach the script to install VB-CABLE.

Auto mode may find and use a VB-CABLE-style output if it is already present, but Windows virtual-cable setup remains a prerequisite outside the script.

### Linux may try to create the recommended virtual mic target automatically

When `--output` is omitted on Linux and no likely bridge-style output is visible, `run` may try the recommended `pactl load-module ...` command once, then rescan.

Keep that behavior fail-fast and concise:

- one attempt
- short user-facing message
- duplicate-aware behavior, if the recommended target already exists but PortAudio still cannot use it, hand off instead of trying to stack another copy
- no retry loop
- no background daemon behavior
- no graph-management ambitions

Also keep the persistence model simple:

- do not auto-unload the created target on exit
- a successfully created target may remain available for later runs

If the target still does not become usable, hand off cleanly.

### A missing model may trigger one automatic download attempt

If constructing `StreamEnhancer` fails in a way that looks like a missing local model, `run` may attempt one automatic `dpdfnet download <model>` and then try loading once more.

Keep this narrow:

- only when the failure looks like a missing model
- one download attempt
- no retry loop
- if loading still fails after the download, hand off clearly to a manual `dpdfnet download <model>` in case local model state is partial or interrupted
- no package-management sprawl

If loading still fails after that, stop with a clear error.

### `list-devices` is the fallback discovery workflow

There is intentionally still no separate `validate` subcommand.

The intended flow is:

1. run `python mic_bridge.py` to view top-level help
2. choose the explicit workflow you want: `run` for live bridging or `list-devices` for discovery
3. if `run` needs help choosing devices safely, run `list-devices`
4. choose or pin device IDs
5. run again with `python mic_bridge.py run ...`
6. stop with Ctrl+C after a quick smoke test

`list-devices` is still the fallback discovery path when `run` cannot choose devices safely, but the no-argument invocation is now help-first rather than run-first.

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

### Default invocation and `run`

The default no-argument command flow is the top-level help screen.

The explicit `run` workflow does these things:

1. resolve the model-native sample rate
2. resolve explicit or auto-selected devices
3. on Linux, optionally try to create the recommended virtual mic target if output auto-selection needs it, but hand off instead of creating duplicates when the target already exists and PortAudio still cannot use it
4. validate same-host-API and device/channel assumptions
5. resolve effective block size and negotiated channel counts
6. print a summary so the user can spot mistakes before audio starts
7. load `StreamEnhancer`, optionally doing one automatic model download if the model looks missing
8. open one duplex stream and run the callback
9. print callback underflow/overflow summary on shutdown

### `list-devices`

`list-devices` does four things:

1. resolves the model-native sample rate
2. chooses a platform-friendly default host API filter
3. probes devices for openability at that sample rate
4. prints a likely pinned command when a good pair is obvious

It is intentionally optimized for fallback discovery and explicit pinning, not for being a raw device dump.

## Where to edit for common changes

If you want to change parser flags or help text, edit `mic_bridge_app/cli.py`.

If you want to change host-API defaults, device tagging, bridge-output identification, channel probing, device scoring, strict auto-output rules, or Linux/JACK block policy, edit `mic_bridge_app/audio_system.py`.

If you want to change what `list-devices` prints, edit `mic_bridge_app/listing.py`.

If you want to change auto-selection flow, Linux virtual mic creation attempts, model auto-download behavior, stream setup, callback logic, or run summaries, edit `mic_bridge_app/runtime.py`.

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
- automatic Windows virtual-cable installation
- silent speaker/headphone selection in auto-output mode

## Guidance for future AI sessions

If you are an AI assistant editing this repo:

- do not collapse the code back into one file
- do not replace straightforward code with many tiny wrappers
- keep auto mode strict about outputs when `run` is invoked, but do not make no-argument startup begin running implicitly
- keep Ctrl+C and failed-run handoffs concise and traceback-free
- keep Linux virtual mic auto-creation duplicate-aware and persistent, do not auto-unload it and do not stack extra copies when PortAudio still cannot use the existing target
- if automatic model download still leaves the model unloadable, hand off to a manual `dpdfnet download <model>` rather than adding retries or hidden cleanup
- do not let auto mode silently route the bridge into speakers/headphones or monitor/source endpoints
- keep Linux virtual mic auto-creation and model auto-download fail-fast and one-shot
- keep architecture rules and user docs aligned with the code
- treat stream topology, sample-rate policy, same-host-API policy, strict auto-output policy, and bridge-output identification policy as deliberate design choices unless the task explicitly asks to redesign them

If you make behavior changes, update the docs in the same patch.
