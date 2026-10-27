# DPDFNet Mic Bridge

Use a noise-reduced microphone in Discord, OBS, games, or other calling apps.
Choose your physical microphone in the desktop window, click **Start**, and
select the bridge's virtual microphone in your calling app. Leave the window
open while streaming; click **Stop** when finished.

## Download and start

Packaged releases include Python, native runtime dependencies, and the default
`dpdfnet8_48khz_hr` model for offline CPU inference. You do not need Python, uv,
Rust, PortAudio development files, or a separate model download.

Get the package for your platform from [GitHub Releases](https://github.com/perennialtech/dpdfnet-mic-bridge/releases):

| Platform | Package | Microphone to select in your calling app |
| --- | --- | --- |
| Windows 10/11 x64 | `*-windows-x64-setup.exe` | `CABLE Output` |
| Linux x86-64 | `*-linux-x86_64.AppImage` | `DPDFNet_Mic` |

Use only the compatibility claims and test results recorded for that release.
CI artifacts are candidates, not evidence of real audio hardware testing.
Windows installers are unsigned and may trigger SmartScreen warnings.

### Windows

Run the installer, then open **DPDFNet Mic Bridge** from the Start menu. The
application installs for your user without administrator access.

Windows separately requires [VB-CABLE from VB-Audio](https://vb-audio.com/Cable/).
The window links to the official download if its endpoints are missing. Driver
installation needs administrator approval and may require a restart. After
installing the driver, click **Refresh**. The bridge writes to `CABLE Input`;
your calling app records from `CABLE Output`.

The bridge does not bundle, install, or uninstall VB-CABLE, which other apps may
also use. The installer is unsigned; Windows may warn about an unknown publisher or
unrecognised application.

### Linux

Use an existing PulseAudio server or PipeWire with its PulseAudio-compatible
service in your normal desktop session. Do not run the bridge with `sudo`.
It creates and verifies `DPDFNet_Mic` when streaming starts and removes its own
virtual endpoints when streaming stops. The desktop profile does not start an
audio server or change global realtime settings.

The build baseline is Ubuntu 22.04 (glibc 2.35), not every Linux distribution.
Your system must support the packaged Qt GUI and its desktop/display libraries.
Mark the AppImage executable in your file manager, then open it. If FUSE is
unavailable, open a terminal in the download directory and use:

```sh
chmod +x dpdfnet-mic-bridge-0.2.0-linux-x86_64.AppImage
./dpdfnet-mic-bridge-0.2.0-linux-x86_64.AppImage --appimage-extract-and-run
```

Replace the version with your downloaded filename. The image includes its
PulseAudio client utilities; installing `pactl` separately is not required.

## Troubleshooting

Open **Show/hide diagnostics** to see startup and streaming errors. If a
microphone is disconnected or renamed, stop and click **Refresh**. On Windows,
confirm both VB-CABLE endpoints appear in Windows sound settings. On Linux,
confirm the desktop audio session permits null sinks and remapped capture
sources. A monitor-only fallback is not accepted as successful setup.

The package does not claim CUDA/GPU support. Audio is processed as mono:
microphone channels are downmixed rather than preserved as stereo. There is no beamforming, channel selector, automatic
gain control, or separate limiter policy.

Application files and the default model are read-only runtime assets. Numba's
cache uses `%LOCALAPPDATA%\dpdfnet-mic-bridge\numba` on Windows and
`${XDG_CACHE_HOME:-~/.cache}/dpdfnet-mic-bridge/numba` on Linux. Reinstall if the
bundled model is reported missing or damaged.

## Command-line use

On Windows, use `dpdfnet-mic-bridge-cli.exe` in the installation directory for
terminal commands. On Linux, pass commands to the AppImage:

```sh
./dpdfnet-mic-bridge-0.2.0-linux-x86_64.AppImage devices
./dpdfnet-mic-bridge-0.2.0-linux-x86_64.AppImage run --input 15
```

Replace `15` with a current microphone ID from `devices`. Linux IDs refer to
PulseAudio sources; Windows IDs refer to native PortAudio/WASAPI devices. IDs can change after reconnecting a device or restarting
the audio session. Stop a terminal stream with `Ctrl+C`.

The basic view uses only the bundled model. Advanced users can supply other
locally obtained ONNX models through `DPDFNET_MODEL_DIR` and `run --model`; the
application never silently downloads them. Use `--help` for the full command
reference. Manual native routes and realtime tuning are advanced features
described in [CONTRIBUTING.md](CONTRIBUTING.md).

## Source builds and licenses

See [CONTRIBUTING.md](CONTRIBUTING.md) for prerequisites, source installation,
tests, packaging, and release checks. The application is MIT licensed;
dependencies and model weights have their own licenses. See the
[third-party notices](packaging/THIRD_PARTY_NOTICES.md) and each release's bundled
`notices` directory. Source downloads are bundled into Qt, Ubuntu client-library,
and third-party notices/Rust source archives rather than individual package files.
Upstream model information is available from
[Ceva's DPDFNet project](https://github.com/ceva-ip/DPDFNet).
