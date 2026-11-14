# DPDFNet Mic Bridge

Reduce microphone background noise in calls, games, and recordings. Choose your
microphone in DPDFNet Mic Bridge, click **Start**, then select the virtual
microphone in your calling or recording app. Keep the bridge open while using it.

## Install

Download the latest package from
[GitHub Releases](https://github.com/perennialtech/dpdfnet-mic-bridge/releases).
Both packages include the application and its default model for offline CPU
processing. No Python or separate model download is needed.

| Platform | Download | Microphone to select in other apps |
| --- | --- | --- |
| Windows 10/11 x64 | `*-windows-x64-setup.exe` | `CABLE Output` |
| Linux x86-64 | `*-linux-x86_64.AppImage` | `DPDFNet_Mic` |

Automated checks do not guarantee compatibility with your microphone or audio
driver. Check each release's notes for any hardware testing.

### Windows

Run the installer and open **DPDFNet Mic Bridge** from the Start menu.

The installer includes [VB-Audio's VB-CABLE](https://www.vb-cable.com), which the
bridge uses as its virtual microphone. On a new installation, you can launch
VB-Audio's driver installer during setup or use **Install VB-CABLE** in the
bridge window later. Driver installation requires administrator approval and a
Windows restart. After restarting, click **Refresh** in the bridge window.

VB-CABLE is a separate, donationware driver from VB-Audio. You can donate or
purchase a license at [VB-Audio's website](https://www.vb-cable.com). Check
[VB-Audio's licensing terms](https://vb-audio.com/Services/licensing.htm) for
commercial or organizational use. The bridge never removes the driver, since
other applications may use it. The DPDFNet Mic Bridge installer is unsigned,
so Windows may show a SmartScreen warning.

### Linux

Run the AppImage in a desktop session with PulseAudio or PipeWire's
PulseAudio-compatible service. Do not run it with `sudo`. The bridge creates
`DPDFNet_Mic` while streaming and removes its virtual audio devices when you
stop.

Make the downloaded AppImage executable in your file manager and open it. If
FUSE is unavailable, run it from a terminal instead, replacing
`downloaded.AppImage` with the file you downloaded:

```sh
chmod +x ./downloaded.AppImage
./downloaded.AppImage --appimage-extract-and-run
```

Linux packages are built for Ubuntu 22.04 (glibc 2.35). Other distributions
may work but are not guaranteed to be compatible.

## Troubleshooting

Open **Show details** in the bridge window to see errors.

- **No microphone listed:** Connect or enable your microphone, then click
  **Refresh**. If a device was disconnected while streaming, stop first.
- **Windows virtual microphone missing:** Complete VB-CABLE installation,
  restart Windows, and click **Refresh**. Check the Windows sound settings for
  both `CABLE Input` and `CABLE Output`.
- **Linux virtual microphone missing:** Check that your desktop audio session
  is running and allows virtual sinks and capture sources.
- **Audio processing warning:** The computer is struggling to keep up with
  inference. Reduce CPU load and check **Show details** for errors.

Processing is CPU-only and uses mono audio. The bridge does not provide GPU
acceleration, beamforming, automatic gain control, or a separate limiter.
If the bundled model is missing or damaged, reinstall the application.

## Command line

On Windows, run `dpdfnet-mic-bridge-cli.exe` from the installation directory.
On Linux, replace `downloaded.AppImage` with your AppImage's filename:

```sh
./downloaded.AppImage devices
./downloaded.AppImage run --input 15
```

Replace `15` with a microphone ID shown by `devices`. IDs can change after a
microphone is reconnected or the audio session restarts. Stop streaming with
`Ctrl+C`.

Run `--help` or `run --help` for the available options. For other locally
obtained ONNX models, set `DPDFNET_MODEL_DIR` and use `run --model`. The bridge
does not download models automatically.

## Source and licenses

See [CONTRIBUTING.md](CONTRIBUTING.md) for source builds, tests, advanced audio
options, and release verification. DPDFNet Mic Bridge is MIT-licensed. Its
model, libraries, and VB-CABLE have separate terms; see the
[third-party notices](packaging/THIRD_PARTY_NOTICES.md) and the notices supplied
with each release. For the upstream model, see [Ceva's DPDFNet](https://github.com/ceva-ip/DPDFNet).
