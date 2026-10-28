## Download

| System | File | Microphone to select in other apps |
| --- | --- | --- |
| Windows 10/11 (x64) | `*-windows-x64-setup.exe` | `CABLE Output` |
| Linux (x86-64) | `*-linux-x86_64.AppImage` | `DPDFNet_Mic` |

Open DPDFNet Mic Bridge, select your microphone, and click **Start**. Then
select the virtual microphone listed above in your calling or recording app.
The default noise-reduction model is included and runs offline on the CPU.

### Requirements

- **Windows:** The installer includes VB-Audio's standard **VB-CABLE** package.
  The driver is required to use the bridge; install it during setup or later
  from the app. Driver installation needs administrator approval and a Windows
  restart. The bridge installer is unsigned, so SmartScreen may show a warning.
- **Linux:** Requires a PulseAudio-compatible desktop audio session, including
  PipeWire with `pipewire-pulse`. The AppImage targets Ubuntu 22.04 (glibc 2.35).

VB-CABLE is donationware by [VB-Audio](https://www.vb-cable.com); you can donate
or purchase a license from its website. See
[VB-Audio licensing](https://vb-audio.com/Services/licensing.htm) for commercial
and organizational use. The driver has separate terms from this application.

Automated tests do not establish compatibility with physical microphones or
all desktop setups. `SHA256SUMS`, third-party notices, and corresponding source
archives are included in the release assets.

[Installation and troubleshooting](https://github.com/perennialtech/dpdfnet-mic-bridge#readme)
