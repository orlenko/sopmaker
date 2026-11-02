"""Preserve the native library's corresponding FFmpeg source and pinned source directions."""

import ctypes
import hashlib
import json
import tarfile
import urllib.request
from pathlib import Path

import av


def preserve(output: Path):
    manifest = json.loads((Path(__file__).parent / "native-sources.json").read_text())
    if av.ffmpeg_version_info != manifest["ffmpeg_version"]:
        raise ValueError("Update and review the native-source manifest for this FFmpeg wheel.")
    destination = output / "native-sources"
    destination.mkdir(exist_ok=True)
    ffmpeg = next(item for item in manifest["sources"] if item["name"] == "ffmpeg")
    cache = Path(__file__).resolve().parents[1] / "build" / "native-cache"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"ffmpeg-{manifest['ffmpeg_version']}.tar.xz"
    if not archive.exists():
        with urllib.request.urlopen(ffmpeg["source_url"], timeout=120) as response:
            archive.write_bytes(response.read())
    if hashlib.sha256(archive.read_bytes()).hexdigest() != ffmpeg["sha256"]:
        archive.unlink(missing_ok=True)
        raise ValueError("The FFmpeg source archive did not match its pinned hash.")
    (destination / archive.name).write_bytes(archive.read_bytes())
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            name = Path(member.name).name
            if member.isfile() and name.startswith(("COPYING", "LICENSE")):
                (destination / name).write_bytes(source.extractfile(member).read())
    directories = [Path(av.__file__).parent, Path(av.__file__).parent.parent / "av.libs"]
    for directory in directories:
        if not directory.exists():
            continue
        for file in directory.rglob("*"):
            if file.is_file() and file.name.startswith(("libavcodec", "avcodec")) and any(ext in file.name for ext in (".so", ".dll", ".dylib")):
                library = ctypes.CDLL(str(file))
                license_fn, config_fn = library.avcodec_license, library.avcodec_configuration
                license_fn.restype = config_fn.restype = ctypes.c_char_p
                manifest["binary_license"] = license_fn().decode()
                manifest["configuration"] = config_fn().decode()
    if "binary_license" not in manifest:
        raise ValueError("Could not identify the packaged FFmpeg library license.")
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
