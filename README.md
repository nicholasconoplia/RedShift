# RedShift

I like to work with my screen being red. This helps block all the blue light, especially when working at night time. However, this didn't exist. You could only use color filters on Mac and F.lux on Windows, which either didn't work on all screens and monitors, or didn't block enough blue light, so I built this to fix that issue. It's open source and free.

## Download

You can download the latest version for macOS and Windows from the [Releases](https://github.com/nicholasconoplia/RedShift/releases) page.

---

Cross-platform red/orange display filter for macOS and Windows.

## Runtime Requirements

- Python 3.10+
- `pystray`
- `pillow`

Install:

```bash
pip install -r requirements.txt
```

## Run From Source

```bash
python redshift.py
```

The app starts in the tray/menu bar, restores the last saved intensity from `~/.redshift/settings.json`, and applies the filter immediately.

## Packaged App

For people you send this to, they should run the packaged app, not the Python script.

- macOS output: `dist/RedShift.app`
- Windows output: `dist/RedShift/RedShift.exe` or a one-file `.exe` if you switch the spec to onefile mode

Build requirements:

- Python 3.10+
- `pip install -r requirements.txt -r requirements-build.txt`

Build:

```bash
python build_release.py
```

The build script generates native icon assets and then runs PyInstaller.

Local signed macOS build:

```bash
/usr/local/bin/python3.12 -m venv .venv312
.venv312/bin/python -m pip install -r requirements.txt -r requirements-build.txt
.venv312/bin/python build_release.py --sign
open dist/RedShift.app
```

The `--sign` flag signs and verifies `dist/RedShift.app` with the Developer ID Application identity configured in `build_release.py`, or with `REDSHIFT_CODESIGN_IDENTITY` / `--identity` if you need to override it. The app still needs notarization before broad distribution.

CI builds:

- `.github/workflows/release-builds.yml` builds macOS Intel, macOS Apple Silicon, and Windows artifacts on Python 3.11
- use those artifacts as the base for signing and final distribution
- macOS builds enforce a deployment target floor in `build_release.py` and delete `dist/` again if validation fails

## Controls

- Tray status shows `OFF`, a percentage, or `MAX`
- `Adjust Filter...` opens the slider window
- `Turn Off` restores the display to normal
- `Quit` restores the display and exits

## Notes

- macOS uses CoreGraphics gamma tables via `ctypes`
- Windows uses `EnumDisplayMonitors`/`GetMonitorInfoW` to target every display in the virtual desktop, including the primary laptop panel, then applies `SetDeviceGammaRamp` per display device context via `ctypes`
- Windows re-applies the ramp every 5 seconds to survive display resets

## Distribution

For broad distribution, a raw unsigned app bundle is not enough.

- macOS:
  - build on macOS
  - sign with a Developer ID Application certificate
  - notarize with Apple
  - staple the notarization ticket before sending it out
- Windows:
  - build on Windows
  - or use the `RedShift-windows-x64` artifact from `.github/workflows/release-builds.yml`
  - sign the executable or installer with an Authenticode code-signing certificate
  - distribute as an installer or signed zip

Without signing, users will still hit Gatekeeper or SmartScreen warnings.

## macOS Compatibility

- local macOS builds currently target `MACOSX_DEPLOYMENT_TARGET=14.0`
- the build script validates bundled binaries after packaging and removes `dist/` if any binary exceeds that target
- on this Mac, Python 3.12 produced a compatible signed local build; Python 3.13 from Homebrew bundled libraries that failed the macOS 14 compatibility check
- for wider compatibility than the local machine can guarantee, use the clean GitHub Actions builds on Python 3.11 and validate on the oldest macOS version you intend to support

## Constraints

- macOS gamma changes require a non-sandboxed app
- Running from Terminal or an unsigned packaged app is fine
