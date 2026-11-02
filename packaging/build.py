"""Build a portable folder on its target OS, retaining notices and source identity."""

import hashlib
import json
import platform
import shutil
import subprocess
import sys
from importlib.metadata import distributions
from pathlib import Path

import av
from native_sources import preserve

ROOT = Path(__file__).resolve().parents[1]


def main():
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
                    "--name", "Sopmaker", "--collect-all", "sopmaker", "--collect-all", "av",
                    "--collect-all", "tzdata", "--collect-submodules", "keyring.backends",
                    "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
                    "--specpath", str(ROOT / "build"), str(ROOT / "packaging" / "desktop.py")], check=True)
    output = ROOT / "dist" / "Sopmaker"
    shutil.copy(ROOT / "LICENSE", output / "LICENSE")
    shutil.copy(ROOT / "README.md", output / "README.md")
    shutil.copy(ROOT / "docs" / "third-party.md", output / "THIRD-PARTY.md")
    preserve(output)
    licenses = output / "licenses"
    licenses.mkdir(exist_ok=True)
    versions = {}
    for dist in distributions():
        name = dist.metadata["Name"]
        versions[name] = dist.version
        for file in dist.files or []:
            if any(part.lower().startswith(("license", "copying", "notice")) for part in file.parts):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    target = licenses / name / str(file).replace("/", "_").replace("\\", "_")
                    target.parent.mkdir(exist_ok=True)
                    shutil.copyfile(source, target)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    info = {"source_revision": revision, "dirty_checkout": dirty, "platform": platform.platform(),
            "python": platform.python_version(), "dependencies": versions,
            "ffmpeg_version": av.ffmpeg_version_info,
            "uv_lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()}
    (output / "build-info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"Portable folder built: {output}")


if __name__ == "__main__":
    main()
