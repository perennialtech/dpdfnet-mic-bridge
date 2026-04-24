# Mic bridge architecture

This is the maintainer handoff note. Read it before changing behavior.

## Purpose of the split

The split keeps the main policy boundaries obvious: entry points, CLI parsing, dependency loading, model/sample-rate rules, audio-system policy, runtime orchestration, notifications, shared state, and long-form design notes. Aim for cohesive modules and direct code. Do not collapse everything into one file, and do not over-split simple workflows into helper-per-concept sprawl.

## File map

```text
mic_bridge.py
    Thin launcher. Keep it boring.

mic_bridge_app/__init__.py
mic_bridge_app/__main__.py
    Package entry points.
mic_bridge_app/app.py
    Top-level command dispatch, no-argument help behavior,
    explicit-subcommand handoffs for run flags,
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
    - AudioInventory
    - DeviceProbe
    - ProbeSnapshot
    - DeviceSelection
    - ResolvedRunSelection
    - RunConfig
    - LinuxVirtualMicLease
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

mic_bridge_app/notifications.py
    Best-effort attention cues for important live-run events.
    Runtime decides when a live event is important enough to surface.
    This module decides how to surface it without adding hard runtime
    dependencies or hiding the terminal handoff.

mic_bridge_app/runtime.py
    The run command:
    - resolve explicit and auto-selected devices against one concrete current
      PortAudio inventory snapshot
    - print short handoffs when auto mode cannot choose safely
    - optionally create the recommended temporary Linux virtual mic target
    - build RunConfig
    - print run summary
    - load StreamEnhancer
    - do a one-shot automatic model download when the model looks missing
    - open the duplex stream
    - keep interrupted/failed-run handoffs concise around temporary Linux target cleanup
    - poll queued live alerts from the callback path
    - run the callback
    - print shutdown status summary
```

## Core invariants

These are the important architectural facts that should not be casually changed.

### Use `dpdfnet.StreamEnhancer`, not raw ONNX inference

The model is not a simple waveform-in, waveform-out black box. Real-time use requires surrounding state, causal STFT, overlap-add ISTFT, and buffering. `StreamEnhancer` is the correct abstraction boundary. Direct ONNX calls inside the callback would be a different design, not a cleanup.

### No live resampling

The stream sample rate must exactly match the model's native sample rate. For `dpdfnet8_48khz_hr`, that means 48,000 Hz. This policy is enforced in `modeling.py`.

### Single full-duplex stream

The bridge uses one `sounddevice.Stream` to avoid manual queueing, drift, and clock-domain problems. Separate input and output streams would be a new architecture.

### Keep the output FIFO

Do not remove `AudioFifo`. `StreamEnhancer.process()` may return fewer or more samples than one callback block, especially around startup or with non-hop-aligned block sizes. The FIFO protects against dropped or duplicated audio and keeps chunk storage plus a read offset instead of rebuilding partially consumed arrays. Keep that low-churn behavior in the callback path.

### Same-host-API enforcement

Input and output must belong to the same PortAudio host API. Recommended choices:

- Windows: WASAPI
- Linux on PipeWire desktops: PipeWire if exposed, otherwise PulseAudio, then JACK, then ALSA

### Keep selection tied to one current PortAudio inventory snapshot

When `run` resolves devices, the selected numeric IDs, host API names, and same-host-API validation should all come from the same current PortAudio device view after any temporary Linux virtual-mic creation attempt.

Do not auto-select from one query and then validate against a different later query unless the design explicitly intends to refresh and re-resolve. If IDs drift between runs, hand off to `list-devices` rather than trying to repair stale IDs by name.

### Auto mode is strict about outputs

Auto mode may select devices, but only under explicit safety rules:

- outputs must look like bridge outputs, not speakers, headphones, or monitor/source endpoints
- if auto mode cannot choose safely, it stops with a short actionable handoff rather than guessing

Manual numeric IDs remain the explicit fallback.

### Interrupted and failed runs hand off cleanly

- Ctrl+C during setup exits cleanly without a traceback dump
- failed stream opens stop with a short actionable handoff
- if pinned IDs stop working, hand off to `list-devices` instead of fuzzy name recovery
- if a temporary Linux virtual mic target was created for the current run, unload it before exit
- if that cleanup fails, hand off briefly with a manual unload command when possible
- do not add retry loops, auto-healing daemons, or broad cleanup logic to hide environment problems
- if automatic model download is interrupted, exit cleanly and hand off to manual `dpdfnet download <model>`

### Manual IDs remain numeric

Numeric IDs are not promised to survive reboots, host-API changes, hotplug events, or audio-session churn. When they go stale, the fallback is `list-devices`, not fuzzy name-based repair.

### Windows requires external VB-CABLE installation

Do not teach the script to install VB-CABLE. Auto mode may use an existing VB-CABLE-style output, but virtual-cable setup remains a prerequisite outside the script.

### Linux virtual mic auto-creation is one-shot and temporary

When `--output` is omitted on Linux and no likely bridge-style output is visible, `run` may try `pactl load-module ...` once, then rescan. Keep that behavior:

- one attempt, short message, no retry loop, no daemon behavior
- duplicate-aware: if the target exists but PortAudio cannot use it, hand off instead of stacking copies
- temporary: if the current run created the target, unload it during normal shutdown and during failed/interrupted exits
- ownership-aware: only unload the target created by the current run, never a pre-existing user-created target
- if a temporary target was created but PortAudio still cannot use it, clean it up before handing off

The recommended `pactl` command is still the manual fallback for users who want a reusable target between runs. It uses `media.class=Audio/Source/Virtual` for better app discoverability and `channels=1 channel_map=mono` to avoid channel-mapping surprises.

### Model auto-download is one-shot

If `StreamEnhancer` fails in a way that looks like a missing model, `run` attempts one `dpdfnet download <model>` and tries again. No retry loop. If loading still fails, stop with a clear error directing to manual download.

### `list-devices` is the fallback discovery workflow

There is intentionally no separate `validate` subcommand. The flow is: run `list-devices`, choose or pin IDs, run again with `python mic_bridge.py run ...`, stop with Ctrl+C after a smoke test.

### Linux/JACK block sizing is intentionally special

If the host API is JACK on Linux and the user omits `--block-ms`, the code chooses 1024 frames at 48 kHz (21.333 ms) instead of raw 10.0 ms (480 frames). Python + PortAudio + JACK often behaves better with JACK-friendly frame counts. Removing this policy requires real glitch testing.

### Output channel negotiation prefers stereo first

Input negotiation prefers mono. Output negotiation prefers 2 channels, then 1, then max channels, because opening some Linux virtual sinks as mono can lead to left-only playback. The bridge signal is still enhanced mono duplicated across all opened output channels. Do not simplify this back to mono-first for output.

### Live runtime alerts are queued out of the callback and stay concise

Important live-run events deserve attention because the bridge often runs in the background, but the callback must stay real-time-safe.

Keep this split:

- the callback may increment counters and queue one-shot alert markers
- the main thread emits the actual user-facing attention cue
- do not print, run subprocesses, or attempt OS notifications from inside the callback
- do not spam on every individual underflow/overflow flag
- first glitch + one escalation is intentional; detailed counts still belong in the shutdown summary
- notification failures are best-effort only and must never replace the terminal handoff

## Code style guardrails

### Keep policy centralized

If a rule is platform-specific, device-selection-specific, or model-specific, keep it in the module that owns that policy. Do not scatter one rule across several files just to make one function shorter.

- sample-rate compatibility: `modeling.py`
- host-API choice, device tagging, bridge-output identification, channel probing, device scoring, auto-output rules, Linux/JACK block policy: `audio_system.py`
- `list-devices` output: `listing.py`
- best-effort attention cues and cross-platform notification behavior: `notifications.py`
- auto-selection flow, Linux target creation and cleanup, model download, stream setup, callback, live alert polling, run summaries: `runtime.py`
- shared structures: `domain.py`
- new models or sample-rate policy: `constants.py` and `modeling.py`

### Prefer direct code over helper sprawl

A small amount of repetition is fine when it keeps control flow obvious. Bad refactors move one-line operations into helpers, add utility modules used once, or split simple workflows into tiny wrappers. Good refactors remove drift-prone duplication, tighten correctness where several places must agree, and keep the main flow readable top-to-bottom.

### Keep executable command argv and display text derived from one source

If a command is both shown to users and executed by the program, do not build the executable subprocess argv by shell-splitting a human-facing display string. Keep an argv form and derive the display string from it, or derive both from one shared source, so docs and subprocess behavior cannot drift.

### Keep docs synchronized

If behavior changes, update README.md, relevant docstrings, and this file in the same change. Documentation is part of the design.

## Command flow

### `run`

1. resolve the model-native sample rate
2. resolve explicit or auto-selected devices
3. on Linux, optionally try to create the temporary virtual mic target if output auto-selection needs it (duplicate-aware)
4. validate same-host-API and device/channel assumptions against the same current PortAudio inventory snapshot used for selection
5. resolve effective block size and negotiated channel counts
6. print a summary so the user can spot mistakes before audio starts
7. load `StreamEnhancer`, optionally doing one automatic model download
8. open one duplex stream, poll queued live alerts, and run the callback
9. unload any temporary Linux virtual mic target created by this run
10. print callback underflow/overflow summary on shutdown

### `list-devices`

1. resolve the model-native sample rate
2. choose a platform-friendly default host API filter
3. probe devices for openability at that sample rate
4. print a likely pinned command when a good pair is obvious

## Where to edit

- parser flags or help text: `cli.py`
- host-API defaults, device tagging, bridge-output identification, channel probing, device scoring, auto-output rules, Linux/JACK block policy: `audio_system.py`
- `list-devices` output: `listing.py`
- best-effort runtime attention cues and desktop/beep behavior: `notifications.py`
- auto-selection flow, Linux target creation, model download, stream setup, callback, live alert polling, run summaries: `runtime.py`
- shared structures: `domain.py`
- new models or sample-rate policy: `constants.py` and `modeling.py`

## Version 0 non-goals

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

If you are editing this repo:

- keep the current file split
- keep `run` explicit
- keep auto-output strict and same-host-API validation snapshot-based
- keep Linux virtual-mic creation one-shot, temporary, and cleanup-aware
- if numeric IDs drift, hand off to `list-devices`
- keep displayed commands and executed argv derived from the same source
- keep model download one-shot
- keep live alerts queued out of the callback and emitted on the main thread
- update docs with behavior changes

Treat stream topology, sample-rate policy, same-host-API enforcement, strict auto-output selection, bridge-output identification, and queued live-alert delivery as deliberate design choices unless the task explicitly redesigns them.
