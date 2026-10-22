# Build, test, and release

## Source setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), Python 3.12,
and a Rust toolchain. CI installs its tools through the
[build environment action](.github/actions/build-environment/action.yml); the
[workflow](.github/workflows/ci.yml) selects managed Python with `UV_PYTHON`.

Ubuntu prerequisites:

```sh
sudo apt install pkg-config portaudio19-dev pulseaudio-utils
```

On Fedora-like systems, use `pkgconf-pkg-config`, `portaudio-devel`, and your
distribution's PulseAudio client utilities. Linux needs a running compatible
desktop audio session.

Windows needs Visual Studio C++ build tools and the Rust MSVC toolchain. Build
PortAudio with vcpkg's dynamic `x64-windows` triplet without ASIO. Before building,
set `VCPKG_ROOT` to that checkout, `VCPKGRS_TRIPLET=x64-windows`, and
`VCPKGRS_DYNAMIC=1`. Set `PORTAUDIO_DLL` to the triplet's `bin\portaudio.dll`
for source execution; Python uses its directory to resolve native DLLs.
Alternatively, set `PORTAUDIO_LIB_DIR` and `PORTAUDIO_LIB_NAME` for
your compatible library.

From the repository root:

```sh
python packaging/stage_model.py
uv sync --locked
uv run --no-sync dpdfnet-mic-bridge
```

Staging downloads only the pinned default model and license card, checks both
hashes, and writes generated assets under `src/dpdfnet_mic_bridge/assets/models/`.
It never copies a developer's user cache. `uv sync` builds
`dpdfnet_mic_bridge._native_audio` with maturin. Build tools are not runtime
dependencies.

## Tests

```sh
uv run --no-sync pytest -q
uv run --no-sync ruff check src tests packaging
uv run --no-sync black --check src tests packaging
uv run --no-sync python -m dpdfnet_mic_bridge --help
uv run --no-sync python -m dpdfnet_mic_bridge self-test
```

Unit tests do not need physical microphones or modify audio-server settings.
Qt tests use the offscreen platform. `self-test` requires staged weights, blocks
Python network connections, and runs synthetic audio through the CPU model.
Frozen Linux CI also disables OS networking with a network namespace. Hosted
runners do not establish real microphone/driver compatibility.

After freezing, run `uv run --no-sync python packaging/audio_smoke.py
dist/dpdfnet-mic-bridge/dpdfnet-mic-bridge` for opt-in Linux integration testing.
It temporarily creates synthetic endpoints in your existing audio session,
checks the public capture name and enhanced audio, and verifies cleanup. It
does not open physical microphones. CI starts a test audio server separately on
its disposable runner; the application itself never starts a server.

## Build a folder bundle

Use a clean checkout on each target OS; PyInstaller does not cross-compile.
Stage assets before the non-editable installation:

```sh
python packaging/stage_model.py
python packaging/stage_qt_notices.py
uv sync --locked --no-dev --group bundle --no-editable
uv build --wheel --out-dir build/release-wheel
uv run --no-sync python packaging/check_wheel.py build/release-wheel
```

Qt staging downloads large, checksum-pinned corresponding source archives to
`build/third-party-sources/` and extracts license notices. Publish those sources
with the binaries. Review the source manifest when changing Qt versions.

Qt notice staging requires a fresh `build/qt-notices/`; use `--notices-dir` to
inspect notices in another empty directory. Compact filenames are mapped to
their original source paths in `index.json`, avoiding Windows path-length limits.

On Linux, stage the native clients:

```sh
uv run --no-sync python packaging/stage_linux_clients.py
```

On Windows, set `PORTAUDIO_DLL` to that vcpkg checkout's
`installed\x64-windows\bin\portaudio.dll`. Keep its runtime DLLs on `PATH` while
freezing. On either platform:

```sh
uv run --no-sync python packaging/stage_notices.py
uv run --no-sync python -m PyInstaller --clean --noconfirm packaging/dpdfnet-mic-bridge.spec
```

The spec requires assets and notices and fails on missing required metadata.
It uses **onedir**, not onefile. Keep all of `dist/dpdfnet-mic-bridge/`, including
`_internal`; never ship only the executable. Windows also provides a console
executable for diagnostics and child processes. Test `--help`, `self-test`, and
`_desktop-ui-test` before creating the final package.

## Windows installer and signing

Use the Inno Setup version pinned in CI:

```powershell
iscc /DAppVersion=0.1.0 packaging/dpdfnet-mic-bridge.iss
```

The installer writes the application under the current user's Programs directory
and adds a Start menu shortcut and uninstaller. It never manages VB-CABLE.
CI tests installation, upgrade, offline inference, and uninstall in a path with
spaces. Sign both application executables before building the installer, then
sign the final installer with timestamping.

`packaging/sign_windows.ps1` requires Windows SDK `signtool`, a base64 PFX in
`WINDOWS_SIGNING_CERTIFICATE`, and its password in `WINDOWS_SIGNING_PASSWORD`.
Use a suitable hardware-backed signing service instead when your certificate
cannot be exported. Configure secrets in a protected GitHub `release` environment.

## Linux AppImage

```sh
uv run --no-sync python packaging/build_appimage.py
```

The builder requires a fresh `build/AppDir/` and copies the folder bundle,
desktop file, icon, and `AppRun`. It uses checksum-pinned linuxdeploy and
appimagetool. Native audio helpers have a separate dependency search path;
their closure excludes glibc. Do not copy entire host library directories or
launch an audio server from `AppRun`.

Build on Ubuntu 22.04 and test that baseline. An image built on Arch is only a
local development check, not evidence of Ubuntu compatibility.

## Release workflow and gates

PRs run Windows/Linux tests and package unsigned candidates. A tag matching
`v<project.version>` requires Windows signing secrets. Protect the GitHub
`release` environment with human approval. Only the release job has
`contents: write`; it uploads packages, checksums, sources, and notices to a
**draft** GitHub Release.

Record these checks before publishing the draft. Signing credentials, legal
review, and hardware tests cannot be replaced by hosted CI.

- Audit Python/Rust licenses, Qt third-party attributions, native libraries,
  model licensing, and corresponding sources. The weights' Apache-2.0 declaration
  is in their pinned model card, separate from this application's MIT license.
  Audit libraries automatically selected by PyInstaller as well as staged clients.
- Confirm each LGPL component's corresponding sources and required build
  materials are available with the release. CI retains Qt/Rust sources and
  matching Ubuntu client-library sources. Superseded Ubuntu source versions are
  fetched from Launchpad and checked against their exact source descriptors.
  Review wheel-bundled native components
  too; a list of license names is not sufficient.
- Verify timestamped Windows signatures and final checksums. Do not bundle
  VB-CABLE without permission, invent silent driver switches, or uninstall its
  shared driver with the bridge.
- Test clean Windows 10/11 x64 without Python/build tools, with and without
  VB-CABLE: both WASAPI endpoints, physical microphones, real streaming, paths
  with spaces, shutdown, upgrades, and uninstall.
- Test Ubuntu 22.04 and every claimed Linux desktop as an ordinary user with
  PulseAudio and PipeWire-Pulse, including capture visibility in Discord/OBS,
  real streaming, missing-server errors, and FUSE/extract-and-run operation.
- Test offline with empty caches and read-only installation files. Missing or
  corrupt default weights must fail without initiating a model download.
- Test stop during initialization, client disconnects, inference failure, a stuck
  child, window closure, and simultaneous runs. Remove only endpoints owned by
  that run; preserve existing user audio endpoints.

Publish through GitHub's draft release controls after review. No Flatpak or GPU
support is implied by these artifacts.

## Advanced audio and CLI controls

The desktop profile uses `--realtime off`, without PAM, CPU-governor, or global
PipeWire quantum changes. `realtime status` inspects readiness. `realtime apply`
previews persistent changes; `realtime apply --yes` performs them and can need
elevation. These are advanced administration commands, not first-run setup.
Linux `--realtime auto`/`strict` on manual native routes can change session-wide
PipeWire settings. Use `--help` for the complete option reference.

Windows uses Rust/PortAudio WASAPI and validates VB-CABLE endpoint roles. Linux's
ordinary path lists PulseAudio sources and transports raw mono float32 audio
through native `parec`/`pacat` clients. It does not expect ALSA/JACK PortAudio
to enumerate PulseAudio sinks. Neither path uses a Python PortAudio callback or
requires `sounddevice`.

`devices --all-apis` lists advanced PortAudio devices. Explicitly passing
`run --input <id> --output <id>` on Linux uses the native manual route, with
**PortAudio IDs for both devices**, not ordinary PulseAudio source IDs. You must
create a suitable virtual playback endpoint yourself. Do not select speakers,
headphones, capture endpoints, or monitors as outputs. The native engine downmixes
input to mono and copies enhanced mono samples to each selected output channel.
Full-duplex probes open callback streams, matching the native runtime. Host-API
scoring names alone do not prove a particular PortAudio build supports a backend.
