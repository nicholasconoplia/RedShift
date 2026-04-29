#!/usr/bin/env python3
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw


APP_NAME = "RedShift"
APP_VERSION = "1.0.0"
MACOS_DEPLOYMENT_TARGET = "14.0"
ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"
ASSETS_DIR = ROOT / "assets"
ICON_PNG = ASSETS_DIR / "redshift.png"
ICON_ICO = ASSETS_DIR / "redshift.ico"
ICON_ICNS = ASSETS_DIR / "redshift.icns"
SPEC_FILE = ROOT / "redshift.spec"
DEFAULT_SIGNING_IDENTITY = "Developer ID Application: Nicholas Conoplia (BZ8A567N3B)"


def check_python() -> None:
    if os.environ.get("REDSHIFT_ALLOW_OLD_PYTHON") == "1":
        print("Warning: building with an older Python interpreter because REDSHIFT_ALLOW_OLD_PYTHON=1 is set.")
        return
    if sys.version_info < (3, 10):
        raise SystemExit(
            f"{APP_NAME} release builds require Python 3.10+; current interpreter is "
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}."
        )


def run(cmd: list[str], env: dict[str, str] | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, env=env)


def generate_master_icon() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    size = 1024
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.ellipse((64, 64, 960, 960), fill=(195, 18, 18, 255))
    draw.ellipse((64, 64, 960, 960), outline=(50, 10, 10, 255), width=24)
    draw.ellipse((190, 160, 870, 690), fill=(255, 130, 28, 235))
    draw.ellipse((250, 230, 820, 570), fill=(255, 214, 138, 180))

    image.save(ICON_PNG)
    image.save(ICON_ICO, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])


def build_icns() -> None:
    if sys.platform != "darwin":
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        iconset_dir = Path(tmpdir) / "redshift.iconset"
        iconset_dir.mkdir()
        source = Image.open(ICON_PNG)
        sizes = [16, 32, 64, 128, 256, 512]
        for size in sizes:
            resized = source.resize((size, size), Image.LANCZOS)
            resized.save(iconset_dir / f"icon_{size}x{size}.png")
            retina = source.resize((size * 2, size * 2), Image.LANCZOS)
            retina.save(iconset_dir / f"icon_{size}x{size}@2x.png")
        run(["iconutil", "-c", "icns", str(iconset_dir), "-o", str(ICON_ICNS)])


def clean() -> None:
    for path in (BUILD_DIR, DIST_DIR):
        if path.exists():
            shutil.rmtree(path)


def ensure_builder_deps() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "Missing build dependency: PyInstaller. Install build dependencies with "
            "`pip install -r requirements-build.txt`."
        ) from exc


def build() -> None:
    env = os.environ.copy()
    if sys.platform == "darwin":
        env["MACOSX_DEPLOYMENT_TARGET"] = MACOS_DEPLOYMENT_TARGET
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            str(SPEC_FILE),
        ],
        env=env,
    )


def sign_macos_app(identity: str) -> None:
    if sys.platform != "darwin":
        return

    app_path = DIST_DIR / f"{APP_NAME}.app"
    if not app_path.exists():
        raise SystemExit(f"Cannot sign missing app bundle: {app_path}")

    run(
        [
            "codesign",
            "--force",
            "--deep",
            "--options",
            "runtime",
            "--timestamp",
            "--sign",
            identity,
            str(app_path),
        ]
    )
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app_path)])


def validate_macos_compatibility() -> None:
    if sys.platform != "darwin":
        return

    max_allowed = tuple(int(part) for part in MACOS_DEPLOYMENT_TARGET.split("."))
    offenders: list[tuple[str, str]] = []

    for path in DIST_DIR.rglob("*"):
        if not path.is_file():
            continue
        try:
            result = subprocess.run(
                ["otool", "-l", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:
            continue

        min_versions = re.findall(r"minos ([0-9.]+)", result.stdout)
        if not min_versions:
            continue

        highest = max(
            (tuple(int(piece) for piece in version.split(".")), version) for version in min_versions
        )
        if highest[0] > max_allowed:
            offenders.append((highest[1], str(path)))

    if offenders:
        for version, path in offenders:
            print(f"Incompatible macOS target {version}: {path}", file=sys.stderr)
        clean()
        raise SystemExit(
            f"{APP_NAME} build failed compatibility validation. "
            f"One or more bundled binaries require newer than macOS {MACOS_DEPLOYMENT_TARGET}."
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Build {APP_NAME} desktop releases.")
    parser.add_argument("--no-clean", action="store_true", help="Keep existing build and dist directories.")
    parser.add_argument(
        "--sign",
        action="store_true",
        help="Sign the macOS .app bundle after building. Uses --identity or REDSHIFT_CODESIGN_IDENTITY.",
    )
    parser.add_argument(
        "--identity",
        default=os.environ.get("REDSHIFT_CODESIGN_IDENTITY", DEFAULT_SIGNING_IDENTITY),
        help="Code signing identity to use with --sign.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    check_python()
    ensure_builder_deps()
    generate_master_icon()
    if sys.platform == "darwin":
        build_icns()
    if not args.no_clean:
        clean()
    build()
    validate_macos_compatibility()
    if args.sign:
        sign_macos_app(args.identity)


if __name__ == "__main__":
    main()
