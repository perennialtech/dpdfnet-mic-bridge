# Third-party software and model

DPDFNet Mic Bridge is MIT licensed. That license does not replace the licenses
of the included interpreter, libraries, audio clients, or model weights.
The `notices` directory contains license texts, package metadata, and native
dependency provenance for this build.

## Default model

The unmodified `dpdfnet8_48khz_hr.onnx` weights are from
[Ceva-IP/DPDFNet](https://huggingface.co/Ceva-IP/DPDFNet). Their revision,
SHA-256, and the model card declaring Apache-2.0 are recorded in
`assets/models/default.json`. The model card and Apache license are included
with the release notices. Credit: Daniel Rika, Nino Sapir, and Ido Gus, Ceva.

## Qt for Python

This application uses the open-source, dynamically linked Qt for Python
(PySide6 and Shiboken6) and Qt libraries under LGPL-3.0, with applicable
third-party exceptions. The application does not restrict replacement of those
libraries or reverse engineering for debugging modifications to them.

Windows installs the complete folder bundle. Linux AppImages can be extracted
with `--appimage-extract`; run the extracted `AppRun` after replacing compatible
libraries. Qt and Shiboken shared libraries live in `_internal`, either at its
root or under package subdirectories. Preserve compatible names and ABI when replacing
them. Do not replace the application's Python or model files to replace Qt.

`notices/qt/index.json` maps compact notice filenames to their original paths
inside the matching source archives. This keeps installer paths short without
discarding the upstream license and attribution texts.

The release's source materials must include the corresponding Qt/PySide sources
and their third-party notices. See the release checklist in `CONTRIBUTING.md`.
The complete licensing reference is [Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html).

## Audio components

Linux includes PulseAudio client utilities and client libraries, not an audio
server. The native dependency inventory and available distribution copyright
files are included in the notices. LGPL components require corresponding source
materials when distributing binaries; the release checklist covers this audit.

PortAudio and the Rust extension are included for Windows and advanced native
routes.

The Windows setup bundles the **unmodified standard VB-CABLE driver** by
**VB-Audio** (Vincent Burel), which is separate donationware, *not* MIT licensed.
All contributions are welcome. End users may donate or purchase their licenses
at [www.vb-cable.com](https://www.vb-cable.com). VB-Audio's
[published distribution grant](https://vb-audio.com/Services/licensing.htm)
permits embedding the standard VB-CABLE with another application when end users
can identify VB-Audio and donate or buy a license. Professional deployments
where users cannot pay for their own licenses require the distributor to buy
them; the extra A+B and C+D driver editions may not be bundled.
The original vendor `readme.txt` is retained in `vendor/vbcable` with the
driver files. The archive source and SHA-256 are pinned in `packaging/vbcable.json`.
Installer and in-app installation are optional, require administrator approval,
and use VB-Audio's GUI. The bridge never uninstalls or replaces the driver.
