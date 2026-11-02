"""Bounded media preparation using bundled FFmpeg libraries through PyAV."""

import array
import json
import wave
from pathlib import Path

import av
from PIL import Image, ImageOps

from .metadata import capture_time

PHOTOS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEOS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
AUDIO = {".m4a", ".mp3", ".wav", ".aac", ".ogg", ".flac"}
SUPPORTED = PHOTOS | VIDEOS | AUDIO
PREPARATION_VERSION = 1
MAX_SECONDS = 30 * 60
MAX_FRAMES = 12
CHUNK_SAMPLES = 5 * 60 * 16000


def save_json(path: Path, data) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _save_image(image: Image.Image, path: Path) -> None:
    image = image.convert("RGB")
    image.thumbnail((1800, 1800))
    image.save(path, "JPEG", quality=88)


def prepare(source: Path, output: Path, asset_id: str, zone: str) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    result = {"version": PREPARATION_VERSION, "images": [], "audio": [], "duration": None}
    if source.suffix.lower() in PHOTOS:
        with Image.open(source) as image:
            exif = image.getexif()
            nested = exif.get_ifd(34665) if 34665 in exif else {}
            raw = nested.get(36867) or exif.get(36867)
            offset = nested.get(36881) or exif.get(36881)
            if raw and offset:
                raw = str(raw) + str(offset)
            result["capture_time"] = capture_time(raw, zone)
            _save_image(ImageOps.exif_transpose(image), output / "photo.jpg")
        result["images"].append({"id": f"{asset_id}:photo", "file": "photo.jpg", "seconds": None})
        result["kind"] = "photo"
    else:
        with av.open(str(source)) as container:
            duration = container.duration / av.time_base if container.duration else None
            result["duration"] = duration
            if duration is not None and duration > MAX_SECONDS:
                raise ValueError("This recording exceeds 30 minutes. Split it into shorter fragments.")
            result["capture_time"] = capture_time(container.metadata.get("creation_time"), zone)
            result["kind"] = "video" if container.streams.video else "audio"
            if not container.streams.video and not container.streams.audio:
                raise ValueError("No supported audio or video stream was found.")
        if result["kind"] == "video":
            result["images"] = _frames(source, output, asset_id, duration)
        result["audio"] = _audio(source, output)
    save_json(output / "prepared.json", result)
    return result


def _frames(source: Path, output: Path, asset_id: str, duration: float | None) -> list[dict]:
    images = []
    with av.open(str(source)) as container:
        stream = container.streams.video[0]
        rotation = int(float(stream.metadata.get("rotate", "0"))) % 360
        # Seek to a bounded set of moments, then retain the actual decoded frame time.
        targets = [i * max(0, (duration or 1) - 0.05) / MAX_FRAMES for i in range(MAX_FRAMES)]
        seen = set()
        for target in targets:
            container.seek(int(target * av.time_base), backward=True)
            for frame in container.decode(video=0):
                seconds = float(frame.time or 0)
                if seconds + 0.02 < target:
                    continue
                if seconds in seen:
                    break
                seen.add(seconds)
                image = frame.to_image()
                frame_rotation = int(getattr(frame, "rotation", 0) or rotation) % 360
                if frame_rotation:
                    image = image.rotate(-frame_rotation, expand=True)
                filename = f"frame-{len(images):02}.jpg"
                _save_image(image, output / filename)
                images.append({"id": f"{asset_id}:frame:{len(images)}", "file": filename,
                               "seconds": round(seconds, 3)})
                break
    if not images:
        raise ValueError("No video frames could be decoded.")
    return images


def _audio(source: Path, output: Path) -> list[dict]:
    chunks = []
    total = 0
    writer = None
    chunk_samples = 0
    peak = 0

    def consume(frame):
        nonlocal total, writer, chunk_samples, peak
        data = bytes(frame.planes[0])[:frame.samples * 2]
        values = array.array("h", data)
        if values:
            peak = max(peak, max(abs(value) for value in values))
        position = 0
        while position < len(data):
            if writer is None:
                filename = f"audio-{len(chunks):03}.wav"
                writer = wave.open(str(output / filename), "wb")
                writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                chunks.append({"file": filename, "start": total / 16000, "end": None})
                chunk_samples = 0
            take = min(len(data) - position, (CHUNK_SAMPLES - chunk_samples) * 2)
            writer.writeframesraw(data[position:position + take])
            count = take // 2
            total += count
            chunk_samples += count
            position += take
            if total > MAX_SECONDS * 16000:
                raise ValueError("Decoded audio exceeds the 30-minute fragment limit.")
            chunks[-1]["end"] = total / 16000
            if chunk_samples == CHUNK_SAMPLES:
                writer.close()
                writer = None

    try:
        with av.open(str(source)) as container:
            if not container.streams.audio:
                return []
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    consume(converted)
            for converted in resampler.resample(None):
                consume(converted)
    finally:
        if writer:
            writer.close()
    # Digital silence is not evidence of speech. Do not send it to transcription.
    if peak < 32:
        for chunk in chunks:
            (output / chunk["file"]).unlink(missing_ok=True)
        return []
    return chunks
