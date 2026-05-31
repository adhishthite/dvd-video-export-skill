#!/usr/bin/env python3
"""Safe DVD-Video export helper.

This script is intentionally conservative: it treats source DVD folders as
read-only and writes only to a separate export directory.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


VIDEO_EXTS = {".vob", ".ifo", ".bup"}
DEFAULT_SAMPLES = ("00:05:00", "01:30:00", "03:00:00")
FORMAT_PRESETS = {
    "hevc-mp4": {
        "extension": ".mp4",
        "label": "HEVC",
        "default_encoder": "hevc_videotoolbox",
        "expected_video_codecs": {"hevc", "h265"},
        "tag": "hvc1",
        "faststart": True,
    },
    "h264-mp4": {
        "extension": ".mp4",
        "label": "H264",
        "default_encoder": "h264_videotoolbox",
        "expected_video_codecs": {"h264"},
        "tag": "avc1",
        "faststart": True,
    },
    "hevc-mkv": {
        "extension": ".mkv",
        "label": "HEVC",
        "default_encoder": "hevc_videotoolbox",
        "expected_video_codecs": {"hevc", "h265"},
        "tag": None,
        "faststart": False,
    },
    "h264-mkv": {
        "extension": ".mkv",
        "label": "H264",
        "default_encoder": "h264_videotoolbox",
        "expected_video_codecs": {"h264"},
        "tag": None,
        "faststart": False,
    },
}


@dataclass
class Disc:
    root: Path
    video_ts: Path
    vobs: list[Path]
    title_set: str = ""
    duration: float | None = None


class ValidationError(RuntimeError):
    """Raised when a completed export does not satisfy safety checks."""


def fail(message: str, code: int = 2) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def run(cmd: list[str], *, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            check=check,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except FileNotFoundError:
        fail(f"required command not found: {cmd[0]}")


def require_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            fail(f"{tool} is required")


def resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def safe_title(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", " ", value).strip()
    value = re.sub(r"\s+", " ", value)
    return value or "DVD Export"


def reject_unsafe_path(path: Path) -> None:
    text = str(path)
    if "\n" in text or "\r" in text:
        fail(f"refusing path with newline characters: {path}")
    if "|" in text:
        fail(f"refusing path with '|' because ffmpeg concat protocol cannot represent it safely: {path}")


def ffconcat_quote(path: Path) -> str:
    text = str(path)
    if "\n" in text or "\r" in text:
        fail(f"refusing path with newline characters: {path}")
    return "'" + text.replace("'", "'\\''") + "'"


def natural_key(path: Path) -> list[object]:
    parts = re.split(r"(\d+)", str(path).lower())
    return [int(p) if p.isdigit() else p for p in parts]


def find_video_ts_roots(root: Path) -> list[Path]:
    if root.name.upper() == "VIDEO_TS":
        return [root]
    found = [p for p in root.rglob("*") if p.is_dir() and p.name.upper() == "VIDEO_TS"]
    return sorted(found, key=natural_key)


def title_set_vobs(video_ts: Path) -> dict[str, list[Path]]:
    vobs = [p for p in video_ts.iterdir() if p.is_file() and p.suffix.lower() == ".vob"]
    groups: dict[str, list[Path]] = {}
    for path in vobs:
        match = re.match(r"(VTS_\d+)_([1-9]\d*)\.VOB$", path.name, re.I)
        if match:
            groups.setdefault(match.group(1).upper(), []).append(path)
    return {key: sorted(paths, key=natural_key) for key, paths in sorted(groups.items())}


def main_vobs(video_ts: Path) -> list[Path]:
    groups = title_set_vobs(video_ts)
    if groups:
        return sorted(groups.values(), key=lambda paths: sum(p.stat().st_size for p in paths), reverse=True)[0]
    vobs = [p for p in video_ts.iterdir() if p.is_file() and p.suffix.lower() == ".vob"]
    return sorted(vobs, key=natural_key)


def discover_discs(input_path: Path, title_set: str = "auto") -> list[Disc]:
    roots = find_video_ts_roots(input_path)
    discs: list[Disc] = []
    for video_ts in roots:
        groups = title_set_vobs(video_ts)
        if groups:
            normalized = title_set.upper()
            if title_set.lower() == "auto":
                selected, vobs = sorted(
                    groups.items(),
                    key=lambda item: sum(p.stat().st_size for p in item[1]),
                    reverse=True,
                )[0]
            else:
                selected = normalized
                if selected not in groups:
                    fail(f"title set {title_set} not found in {video_ts}; available: {', '.join(groups)}")
                vobs = groups[selected]
        else:
            selected = ""
            vobs = main_vobs(video_ts)
        if not vobs:
            continue
        for path in vobs:
            reject_unsafe_path(path)
        discs.append(Disc(root=video_ts.parent, video_ts=video_ts, vobs=vobs, title_set=selected))
    return discs


def ffprobe_duration(path_or_url: str) -> float | None:
    proc = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            path_or_url,
        ],
        capture=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        value = json.loads(proc.stdout)["format"].get("duration")
        return float(value) if value is not None else None
    except Exception:
        return None


def parse_rate(rate: str | None) -> float | None:
    if not rate or rate == "0/0":
        return None
    if "/" in rate:
        num, den = rate.split("/", 1)
        try:
            denominator = float(den)
            return float(num) / denominator if denominator else None
        except ValueError:
            return None
    try:
        return float(rate)
    except ValueError:
        return None


def ffprobe_video_info(path_or_url: str) -> dict:
    proc = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,avg_frame_rate,r_frame_rate,field_order",
            "-of",
            "json",
            path_or_url,
        ],
        capture=True,
        check=False,
    )
    if proc.returncode != 0:
        return {}
    try:
        streams = json.loads(proc.stdout).get("streams", [])
        return streams[0] if streams else {}
    except Exception:
        return {}


def ffprobe_audio_info(path_or_url: str) -> dict:
    proc = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,channels,channel_layout,bit_rate",
            "-of",
            "json",
            path_or_url,
        ],
        capture=True,
        check=False,
    )
    if proc.returncode != 0:
        return {}
    try:
        streams = json.loads(proc.stdout).get("streams", [])
        return streams[0] if streams else {}
    except Exception:
        return {}


def concat_url(files: list[Path]) -> str:
    for path in files:
        reject_unsafe_path(path)
    return "concat:" + "|".join(str(f) for f in files)


def video_filter_for_disc(disc: Disc, args: argparse.Namespace) -> str:
    info = ffprobe_video_info(concat_url(disc.vobs))
    fps = parse_rate(info.get("avg_frame_rate")) or parse_rate(info.get("r_frame_rate")) or 25.0
    field_order = (info.get("field_order") or "unknown").lower()
    filters: list[str] = []
    should_deinterlace = args.deinterlace == "always" or (
        args.deinterlace == "auto" and field_order not in {"progressive", "unknown", "undetermined"}
    )
    if should_deinterlace:
        parity = args.field_order
        if parity == "auto":
            parity = "tff" if "tt" in field_order or "top" in field_order else "bff"
        filters.append(f"bwdif=mode=send_frame:parity={parity}:deint=all")
    if args.regenerate_timestamps:
        filters.append(f"setpts=N/({fps:.6f}*TB)")
    return ",".join(filters) or "null"


def output_format_name(args: argparse.Namespace) -> str:
    return getattr(args, "output_format", "hevc-mp4") or "hevc-mp4"


def format_preset(name: str) -> dict:
    try:
        return FORMAT_PRESETS[name]
    except KeyError:
        fail(f"unknown output format: {name}; choose one of {', '.join(FORMAT_PRESETS)}")


def selected_encoder(args: argparse.Namespace) -> str:
    encoder = getattr(args, "encoder", "auto") or "auto"
    if encoder == "auto":
        return format_preset(output_format_name(args))["default_encoder"]
    return encoder


def video_codec_label(args: argparse.Namespace) -> str:
    return format_preset(output_format_name(args))["label"]


def output_extension(args: argparse.Namespace) -> str:
    return format_preset(output_format_name(args))["extension"]


def add_container_options(cmd: list[str], args: argparse.Namespace) -> None:
    preset = format_preset(output_format_name(args))
    if preset["tag"]:
        cmd.extend(["-tag:v", preset["tag"]])
    if preset["faststart"]:
        cmd.extend(["-movflags", "+faststart"])


def split_extra_args(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        return shlex.split(value)
    except ValueError as exc:
        fail(f"could not parse extra ffmpeg args {value!r}: {exc}")


def audio_encoder(args: argparse.Namespace) -> str:
    return getattr(args, "audio_encoder", "aac") or "aac"


def expected_audio_codec_names(args: argparse.Namespace) -> set[str]:
    encoder = audio_encoder(args)
    aliases = {
        "aac": {"aac"},
        "libfdk_aac": {"aac"},
        "libopus": {"opus"},
        "opus": {"opus"},
        "mp3": {"mp3"},
        "libmp3lame": {"mp3"},
        "ac3": {"ac3"},
    }
    return aliases.get(encoder, {encoder})


def scan(args: argparse.Namespace) -> None:
    require_tools()
    roots = [resolved(Path(p)) for p in args.paths]
    rows = []
    for root in roots:
        if not root.exists():
            print(f"missing: {root}", file=sys.stderr)
            continue
        for disc in discover_discs(root, getattr(args, "title_set", "auto")):
            dvd_files = [
                p
                for p in disc.video_ts.iterdir()
                if p.is_file() and p.suffix.lower() in VIDEO_EXTS
            ]
            size = sum(p.stat().st_size for p in disc.root.rglob("*") if p.is_file())
            duration = ffprobe_duration(concat_url(disc.vobs))
            rows.append((disc.root, disc.title_set or "all", size, len(dvd_files), len(disc.vobs), duration))
    if not rows:
        print("No VIDEO_TS DVD exports found.")
        return
    for root, title_set, size, dvd_count, vob_count, duration in rows:
        size_gb = size / (1024**3)
        duration_text = fmt_time(duration) if duration else "unknown"
        print(f"{size_gb:5.1f}G  {duration_text:>10}  {title_set:>6}  {dvd_count:2d} DVD files  {vob_count:2d} VOBs  {root}")


def fmt_time(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "unknown"
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}"


def ask_text(prompt: str, default: str | None = None, *, required: bool = True) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            value = input(f"{prompt}{suffix}: ").strip()
        except EOFError:
            if default is not None:
                print()
                return default
            if not required:
                print()
                return ""
            raise
        if value:
            return value
        if default is not None:
            return default
        if not required:
            return ""
        print("Please enter a value.")


def ask_choice(prompt: str, choices: list[str], default: str) -> str:
    choice_text = "/".join(choices)
    while True:
        value = ask_text(f"{prompt} ({choice_text})", default).lower()
        matches = [choice for choice in choices if choice.startswith(value)]
        if len(matches) == 1:
            return matches[0]
        if value in choices:
            return value
        print(f"Choose one of: {', '.join(choices)}")


def ask_float(prompt: str, default: float, *, min_value: float, max_value: float) -> float:
    while True:
        raw = ask_text(prompt, str(default))
        try:
            value = float(raw)
        except ValueError:
            print("Enter a number.")
            continue
        if min_value <= value <= max_value:
            return value
        print(f"Enter a value from {min_value} to {max_value}.")


def ask_int(prompt: str, default: int, *, min_value: int, max_value: int) -> int:
    while True:
        raw = ask_text(prompt, str(default))
        try:
            value = int(raw)
        except ValueError:
            print("Enter an integer.")
            continue
        if min_value <= value <= max_value:
            return value
        print(f"Enter a value from {min_value} to {max_value}.")


def ask_bool(prompt: str, default: bool) -> bool:
    default_text = "yes" if default else "no"
    while True:
        value = ask_text(f"{prompt} (yes/no)", default_text).lower()
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Answer yes or no.")


def parse_time(value: str) -> float | None:
    match = re.search(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", value)
    if not match:
        return None
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_speed(value: str) -> str | None:
    match = re.search(r"speed=\s*([0-9.]+x)", value)
    return match.group(1) if match else None


def stream_ffmpeg(cmd: list[str], *, total_duration: float | None, label: str, max_warnings: int = 0) -> None:
    print(f"Starting: {label}")
    start = time.monotonic()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout is not None
    last_progress = 0.0
    warnings = 0
    for line in proc.stdout:
        text = line.rstrip()
        lower = text.lower()
        if "error" in lower or "invalid" in lower or "discontinuity" in lower or "non-monotonic" in lower:
            warnings += 1
            print(text)
        current = parse_time(text)
        if current is None:
            continue
        now = time.monotonic()
        if now - last_progress < 25 and proc.poll() is None:
            continue
        last_progress = now
        elapsed = now - start
        speed = parse_speed(text) or "?"
        if total_duration and current > 0:
            pct = min(100.0, current / total_duration * 100)
            eta = elapsed * (total_duration / current - 1)
            print(f"{label}: {pct:5.1f}%  {fmt_time(current)} / {fmt_time(total_duration)}  speed {speed}  ETA {fmt_time(eta)}")
        else:
            print(f"{label}: {fmt_time(current)} processed  speed {speed}")
    code = proc.wait()
    if code != 0:
        fail(f"ffmpeg failed for {label} with exit code {code}")
    if warnings > max_warnings:
        fail(f"ffmpeg reported {warnings} warning/error lines for {label}; maximum allowed is {max_warnings}")
    if warnings:
        print(f"Warning: {warnings} ffmpeg warning/error lines were observed for {label}.")


def validate_paths(input_path: Path, output_dir: Path, allow_inside: bool, *, create_output: bool, discs: list[Disc] | None = None) -> None:
    if not input_path.exists():
        fail(f"input path does not exist: {input_path}")
    if is_relative_to(output_dir, input_path) and not allow_inside:
        fail("refusing to write output inside the source tree; choose a separate output directory")
    for disc in discs or []:
        if is_relative_to(output_dir, disc.root) and not allow_inside:
            fail(f"refusing to write output inside DVD source folder: {disc.root}")
        if is_relative_to(output_dir, disc.video_ts) and not allow_inside:
            fail(f"refusing to write output inside VIDEO_TS source folder: {disc.video_ts}")
    if create_output:
        output_dir.mkdir(parents=True, exist_ok=True)


def encode_disc(disc: Disc, output: Path, args: argparse.Namespace) -> None:
    duration = ffprobe_duration(concat_url(disc.vobs))
    disc.duration = duration
    vf = video_filter_for_disc(disc, args)
    af = "asetpts=N/SR/TB"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y" if args.overwrite else "-n",
        "-nostdin",
        "-stats_period",
        str(args.stats_period),
        "-i",
        concat_url(disc.vobs),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-vf",
        vf,
        "-af",
        af,
        "-c:v",
        selected_encoder(args),
        "-b:v",
        args.video_bitrate,
        "-maxrate",
        args.maxrate,
        "-bufsize",
        args.bufsize,
        "-c:a",
        audio_encoder(args),
        "-b:a",
        args.audio_bitrate,
    ]
    cmd.extend(split_extra_args(getattr(args, "extra_video_args", "")))
    cmd.extend(split_extra_args(getattr(args, "extra_output_args", "")))
    add_container_options(cmd, args)
    cmd.append(str(output))
    stream_ffmpeg(cmd, total_duration=duration, label=disc.root.name, max_warnings=args.max_warnings)


def join_parts(parts: list[Path], final_output: Path, args: argparse.Namespace) -> None:
    list_path = final_output.with_suffix(".parts.txt")
    list_path.write_text("".join(f"file {ffconcat_quote(p)}\n" for p in parts), encoding="utf-8")
    total_duration = sum((ffprobe_duration(str(p)) or 0) for p in parts)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y" if args.overwrite else "-n",
        "-nostdin",
        "-stats_period",
        str(args.stats_period),
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        "-metadata",
        f"title={args.title}",
    ]
    cmd.extend(split_extra_args(getattr(args, "extra_output_args", "")))
    add_container_options(cmd, args)
    cmd.append(str(final_output))
    stream_ffmpeg(cmd, total_duration=total_duration or None, label="join", max_warnings=args.max_warnings)


def audio_filter(args: argparse.Namespace, input_file: Path | None = None) -> str:
    filters = []
    if args.audio_mode == "dual-mono":
        channels = 2
        if input_file is not None:
            try:
                channels = int(ffprobe_audio_info(str(input_file)).get("channels") or 2)
            except (TypeError, ValueError):
                channels = 2
        if channels <= 1:
            filters.append("pan=stereo|c0=c0|c1=c0")
        elif channels == 2:
            filters.append("pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1")
        else:
            filters.append("pan=stereo|c0=0.25*c0+0.25*c1+0.5*c2|c1=0.25*c0+0.25*c1+0.5*c2")
    if args.volume != 1.0:
        filters.append(f"volume={args.volume}")
    return ",".join(filters) or "anull"


def rewrite_audio(input_file: Path, output_file: Path, args: argparse.Namespace) -> None:
    duration = ffprobe_duration(str(input_file))
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y" if args.overwrite else "-n",
        "-nostdin",
        "-stats_period",
        str(args.stats_period),
        "-i",
        str(input_file),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-c:v",
        "copy",
        "-af",
        audio_filter(args, input_file),
        "-c:a",
        audio_encoder(args),
        "-b:a",
        args.audio_bitrate,
        "-metadata",
        f"title={args.title}",
    ]
    cmd.extend(split_extra_args(getattr(args, "extra_audio_args", "")))
    cmd.extend(split_extra_args(getattr(args, "extra_output_args", "")))
    add_container_options(cmd, args)
    cmd.append(str(output_file))
    stream_ffmpeg(cmd, total_duration=duration, label="audio balance/boost", max_warnings=args.max_warnings)


def ffprobe_summary(path: Path) -> dict:
    proc = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size,bit_rate",
            "-show_entries",
            "stream=index,codec_type,codec_name,width,height,display_aspect_ratio,channels,channel_layout,bit_rate",
            "-of",
            "json",
            str(path),
        ],
        capture=True,
    )
    return json.loads(proc.stdout)


def astats_sample(path: Path, ss: str, duration: int = 60) -> tuple[float | None, float | None, float | None, float | None]:
    proc = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-ss",
            ss,
            "-t",
            str(duration),
            "-i",
            str(path),
            "-af",
            "astats=metadata=1:reset=0",
            "-f",
            "null",
            "-",
        ],
        capture=True,
        check=False,
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    channels: dict[int, dict[str, float]] = {}
    current: int | None = None
    for line in text.splitlines():
        ch = re.search(r"Channel:\s*(\d+)", line)
        if ch:
            current = int(ch.group(1))
            channels.setdefault(current, {})
            continue
        if current is None:
            continue
        peak = re.search(r"Peak level dB:\s*(-?[0-9.]+)", line)
        rms = re.search(r"RMS level dB:\s*(-?[0-9.]+)", line)
        if peak:
            channels[current]["peak"] = float(peak.group(1))
        if rms:
            channels[current]["rms"] = float(rms.group(1))
    left = channels.get(1, {})
    right = channels.get(2, {})
    return left.get("rms"), right.get("rms"), left.get("peak"), right.get("peak")


def validate_output(
    path: Path,
    samples: tuple[str, ...],
    *,
    expected_duration: float | None = None,
    expect_balanced: bool = True,
    expected_video_codecs: set[str] | None = None,
    expected_audio_codecs: set[str] | None = None,
    min_size_bytes: int = 10 * 1024 * 1024,
    duration_tolerance_seconds: float = 5.0,
    duration_tolerance_ratio: float = 0.02,
    balance_tolerance_db: float = 0.25,
    clipping_peak_db: float = -0.1,
    sample_duration: int = 60,
) -> None:
    summary = ffprobe_summary(path)
    fmt = summary.get("format", {})
    errors: list[str] = []
    duration = float(fmt.get("duration", 0) or 0)
    size = int(fmt.get("size", 0) or 0)
    streams = summary.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    print("Validation:")
    print(f"  path: {path}")
    print(f"  duration: {fmt_time(duration)}")
    print(f"  size: {size / (1024**3):.1f}G")
    if not path.exists():
        errors.append("output file does not exist")
    if size < min_size_bytes:
        errors.append(f"output file is too small: {size} bytes")
    if duration <= 0:
        errors.append("output duration is zero or unavailable")
    if expected_duration and duration > 0:
        delta = abs(duration - expected_duration)
        tolerance = max(duration_tolerance_seconds, expected_duration * duration_tolerance_ratio)
        if delta > tolerance:
            errors.append(
                f"duration mismatch: expected {fmt_time(expected_duration)}, got {fmt_time(duration)}"
            )
    if not video_streams:
        errors.append("missing video stream")
    if not audio_streams:
        errors.append("missing audio stream")
    for stream in streams:
        if stream.get("codec_type") == "video":
            allowed_video_codecs = expected_video_codecs or {"hevc", "h265"}
            if stream.get("codec_name") not in allowed_video_codecs:
                errors.append(f"unexpected video codec: {stream.get('codec_name')}")
            print(
                f"  video: {stream.get('codec_name')} {stream.get('width')}x{stream.get('height')} "
                f"{stream.get('display_aspect_ratio', '')}"
            )
        if stream.get("codec_type") == "audio":
            allowed_audio_codecs = expected_audio_codecs or {"aac"}
            if stream.get("codec_name") not in allowed_audio_codecs:
                errors.append(f"unexpected audio codec: {stream.get('codec_name')}")
            if int(stream.get("channels") or 0) != 2:
                errors.append(f"unexpected audio channel count: {stream.get('channels')}")
            print(
                f"  audio: {stream.get('codec_name')} {stream.get('channels')}ch "
                f"{stream.get('channel_layout', '')} {stream.get('bit_rate', '')}bps"
            )
    for ss in samples:
        lrms, rrms, lpeak, rpeak = astats_sample(path, ss, sample_duration)
        if lrms is None or rrms is None:
            errors.append(f"sample {ss}: audio stats unavailable")
            continue
        delta = abs(lrms - rrms)
        peaks = [v for v in (lpeak, rpeak) if v is not None]
        if not peaks:
            errors.append(f"sample {ss}: peak stats unavailable")
            continue
        peak = max(peaks)
        print(f"  sample {ss}: L/R RMS {lrms:.2f}/{rrms:.2f} dB delta {delta:.2f} dB peak {peak:.2f} dB")
        if expect_balanced and delta > balance_tolerance_db:
            errors.append(f"sample {ss}: left/right RMS delta {delta:.2f} dB exceeds {balance_tolerance_db} dB")
        if peak >= clipping_peak_db:
            errors.append(f"sample {ss}: peak {peak:.2f} dB is too close to clipping")
    if errors:
        raise ValidationError("Validation failed:\n  - " + "\n  - ".join(errors))


def export(args: argparse.Namespace) -> None:
    require_tools()
    input_path = resolved(Path(args.input))
    output_dir = resolved(Path(args.output_dir))
    if not input_path.exists():
        fail(f"input path does not exist: {input_path}")
    discs = discover_discs(input_path, args.title_set)
    if not discs:
        fail(f"no VIDEO_TS folders with VOB files found under {input_path}")
    validate_paths(
        input_path,
        output_dir,
        args.allow_output_inside_source,
        create_output=not args.dry_run,
        discs=discs,
    )
    title = safe_title(args.title or input_path.name)
    args.title = title
    audio_label = "Dual Mono" if args.audio_mode == "dual-mono" else "Stereo"
    boost_label = f" +{int(round((args.volume - 1) * 100))}pct Audio" if args.volume != 1.0 else ""
    format_preset(output_format_name(args))
    final_output = output_dir / f"{title} {video_codec_label(args)} {audio_label}{boost_label}{output_extension(args)}"
    if final_output.exists() and not args.overwrite:
        fail(f"output already exists: {final_output}; pass --overwrite or choose another title")
    print(f"Input: {input_path}")
    print(f"Discs/parts: {len(discs)}")
    for i, disc in enumerate(discs, 1):
        duration = ffprobe_duration(concat_url(disc.vobs))
        print(f"  {i}. {disc.root}  title_set={disc.title_set or 'all'}  VOBs={len(disc.vobs)}  duration={fmt_time(duration)}")
    print(f"Output: {final_output}")
    print(f"Format: {output_format_name(args)}, encoder={selected_encoder(args)}")
    print(f"Audio: {args.audio_mode}, volume={args.volume}, encoder={audio_encoder(args)}, bitrate={args.audio_bitrate}")
    if args.dry_run:
        print("Dry run only; no files written.")
        return
    parts: list[Path] = []
    for i, disc in enumerate(discs, 1):
        part = output_dir / f".{title}.part-{i:02d}{output_extension(args)}"
        if part.exists() and args.overwrite:
            part.unlink()
        encode_disc(disc, part, args)
        parts.append(part)
    joined = output_dir / f".{title}.joined{output_extension(args)}"
    if joined.exists() and args.overwrite:
        joined.unlink()
    join_parts(parts, joined, args)
    rewrite_audio(joined, final_output, args)
    expected_duration = sum((ffprobe_duration(str(part)) or 0) for part in parts) or None
    try:
        validate_output(
            final_output,
            tuple(args.samples),
            expected_duration=expected_duration,
            expect_balanced=args.audio_mode == "dual-mono",
            expected_video_codecs=format_preset(output_format_name(args))["expected_video_codecs"],
            expected_audio_codecs=expected_audio_codec_names(args),
            min_size_bytes=getattr(args, "min_size_mb", 10) * 1024 * 1024,
            duration_tolerance_seconds=getattr(args, "duration_tolerance_seconds", 5.0),
            duration_tolerance_ratio=getattr(args, "duration_tolerance_ratio", 0.02),
            balance_tolerance_db=getattr(args, "balance_tolerance_db", 0.25),
            clipping_peak_db=getattr(args, "clipping_peak_db", -0.1),
            sample_duration=getattr(args, "sample_duration", 60),
        )
    except ValidationError as exc:
        print("Validation failed; keeping derived intermediate files for inspection.", file=sys.stderr)
        fail(str(exc))
    if args.keep_intermediates:
        print("Keeping derived intermediate files in output directory.")
    else:
        for path in [*parts, joined, joined.with_suffix(".parts.txt")]:
            if path.exists() and is_relative_to(path.resolve(), output_dir):
                path.unlink()
        print("Removed derived intermediate files from output directory.")
    print("Original source files were not modified.")


def wizard(args: argparse.Namespace) -> None:
    require_tools()
    print("DVD Video Export wizard")
    print("Rule: source DVD/backup files are read-only; output must be a separate derived export folder.")
    input_value = args.input or ask_text("Source DVD folder or parent folder containing VIDEO_TS")
    input_path = resolved(Path(input_value))
    if not input_path.exists():
        fail(f"input path does not exist: {input_path}")

    discs = discover_discs(input_path, args.title_set)
    if not discs:
        fail(f"no VIDEO_TS folders with VOB files found under {input_path}")
    print(f"Found {len(discs)} disc/part(s):")
    for i, disc in enumerate(discs, 1):
        duration = ffprobe_duration(concat_url(disc.vobs))
        print(f"  {i}. {disc.root}  title_set={disc.title_set or 'all'}  VOBs={len(disc.vobs)}  duration={fmt_time(duration)}")

    default_title = safe_title(args.title or input_path.name)
    default_output = str(Path.home() / "Desktop" / f"{default_title} {video_codec_label(args)}")
    output_dir = ask_text("Output folder for derived files", args.output_dir or default_output)
    title = ask_text("Output title", default_title)
    output_format = ask_choice("Output format", list(FORMAT_PRESETS), args.output_format)
    audio_mode = ask_choice("Audio mode", ["dual-mono", "preserve"], args.audio_mode)
    volume = ask_float("Audio volume multiplier", args.volume, min_value=0.5, max_value=2.0)
    encoder = ask_text("Video encoder (auto, hevc_videotoolbox, h264_videotoolbox, libx265, libx264)", args.encoder)
    title_set = ask_text("DVD title set to export", args.title_set)
    deinterlace = ask_choice("Deinterlace", ["auto", "always", "never"], args.deinterlace)
    field_order = ask_choice("Field order", ["auto", "bff", "tff"], args.field_order)
    regenerate_timestamps = ask_bool("Regenerate timestamps from probed frame rate", args.regenerate_timestamps)
    video_bitrate = ask_text("Video bitrate", args.video_bitrate)
    maxrate = ask_text("Video maxrate", args.maxrate)
    bufsize = ask_text("Video rate-control buffer size", args.bufsize)
    extra_video_args = ask_text("Extra ffmpeg video/output args before container options", args.extra_video_args, required=False)
    audio_encoder_value = ask_text("Audio encoder", args.audio_encoder)
    audio_bitrate = ask_text("Audio bitrate", args.audio_bitrate)
    extra_audio_args = ask_text("Extra ffmpeg audio/output args before container options", args.extra_audio_args, required=False)
    extra_output_args = ask_text("Extra ffmpeg output/container args before output path", args.extra_output_args, required=False)
    max_warnings = ask_int("Maximum ffmpeg warning/error lines before failing", args.max_warnings, min_value=0, max_value=1000)
    stats_period = ask_float("Progress update interval passed to ffmpeg stats_period", args.stats_period, min_value=1, max_value=3600)
    sample_text = ask_text("Validation sample start times, comma-separated", ",".join(args.samples))
    samples = [item.strip() for item in sample_text.split(",") if item.strip()]
    sample_duration = ask_int("Validation sample duration in seconds", args.sample_duration, min_value=1, max_value=600)
    min_size_mb = ask_int("Minimum acceptable output size in MB", args.min_size_mb, min_value=1, max_value=1024 * 1024)
    duration_tolerance_seconds = ask_float("Duration tolerance floor in seconds", args.duration_tolerance_seconds, min_value=0, max_value=3600)
    duration_tolerance_ratio = ask_float("Duration tolerance ratio", args.duration_tolerance_ratio, min_value=0, max_value=1)
    balance_tolerance_db = ask_float("Dual-mono L/R RMS balance tolerance in dB", args.balance_tolerance_db, min_value=0, max_value=12)
    clipping_peak_db = ask_float("Maximum allowed peak dBFS before clipping failure", args.clipping_peak_db, min_value=-60, max_value=0)
    allow_output_inside_source = ask_bool("Allow output inside source tree (dangerous; normally no)", False)
    dry_run = ask_bool("Dry-run only first", True)
    keep_intermediates = ask_bool("Keep derived intermediate part files", False)
    overwrite = ask_bool("Overwrite existing derived output if present", False)

    planned = argparse.Namespace(
        input=str(input_path),
        output_dir=output_dir,
        title=title,
        output_format=output_format,
        audio_mode=audio_mode,
        volume=volume,
        title_set=title_set,
        deinterlace=deinterlace,
        field_order=field_order,
        regenerate_timestamps=regenerate_timestamps,
        video_bitrate=video_bitrate,
        maxrate=maxrate,
        bufsize=bufsize,
        extra_video_args=extra_video_args,
        audio_bitrate=audio_bitrate,
        audio_encoder=audio_encoder_value,
        extra_audio_args=extra_audio_args,
        extra_output_args=extra_output_args,
        encoder=encoder,
        max_warnings=max_warnings,
        stats_period=stats_period,
        samples=samples,
        sample_duration=sample_duration,
        min_size_mb=min_size_mb,
        duration_tolerance_seconds=duration_tolerance_seconds,
        duration_tolerance_ratio=duration_tolerance_ratio,
        balance_tolerance_db=balance_tolerance_db,
        clipping_peak_db=clipping_peak_db,
        dry_run=dry_run,
        overwrite=overwrite,
        keep_intermediates=keep_intermediates,
        allow_output_inside_source=allow_output_inside_source,
    )

    print("\nPlanned export:")
    print(f"  source: {planned.input}")
    print(f"  output dir: {planned.output_dir}")
    print(f"  title: {planned.title}")
    print(
        f"  audio: {planned.audio_mode}, volume={planned.volume}, "
        f"encoder={planned.audio_encoder}, bitrate={planned.audio_bitrate}"
    )
    print(f"  title set: {planned.title_set}")
    print(
        f"  video: format={planned.output_format}, encoder={selected_encoder(planned)}, "
        f"bitrate={planned.video_bitrate}, maxrate={planned.maxrate}, bufsize={planned.bufsize}, "
        f"deinterlace={planned.deinterlace}, field_order={planned.field_order}, "
        f"regenerate_timestamps={planned.regenerate_timestamps}"
    )
    print(f"  extra video args: {planned.extra_video_args or '(none)'}")
    print(f"  extra audio args: {planned.extra_audio_args or '(none)'}")
    print(f"  extra output args: {planned.extra_output_args or '(none)'}")
    print(f"  validation samples: {', '.join(planned.samples)} for {planned.sample_duration}s each")
    print(
        f"  validation tolerances: min_size={planned.min_size_mb}MB, "
        f"duration=max({planned.duration_tolerance_seconds}s, {planned.duration_tolerance_ratio:.3f}x), "
        f"balance={planned.balance_tolerance_db}dB, peak<{planned.clipping_peak_db}dBFS"
    )
    print(f"  max ffmpeg warning/error lines: {planned.max_warnings}")
    print(f"  progress stats period: {planned.stats_period}s")
    print(f"  allow output inside source: {planned.allow_output_inside_source}")
    print(f"  dry-run: {planned.dry_run}")
    print(f"  keep intermediates: {planned.keep_intermediates}")
    print(f"  overwrite: {planned.overwrite}")

    if not planned.dry_run:
        confirm = ask_text("Type EXPORT to start the real encode", "", required=False)
        if confirm != "EXPORT":
            print("Cancelled before encoding. No files written.")
            return
    export(planned)


def clean(args: argparse.Namespace) -> None:
    output_dir = resolved(Path(args.output_dir))
    if not output_dir.exists():
        fail(f"output directory does not exist: {output_dir}")
    patterns = [
        "*.part-*.mp4",
        ".*.part-*.mp4",
        "*.part-*.mkv",
        ".*.part-*.mkv",
        "*.part-*.mov",
        ".*.part-*.mov",
        "*.joined.mp4",
        ".*.joined.mp4",
        "*.joined.mkv",
        ".*.joined.mkv",
        "*.joined.mov",
        ".*.joined.mov",
        "*.parts.txt",
        "parts.txt",
        "vob-concat-list.txt",
    ]
    removed = []
    for pattern in patterns:
        for path in output_dir.glob(pattern):
            if path.is_file() and is_relative_to(path.resolve(), output_dir):
                path.unlink()
                removed.append(path)
    for path in removed:
        print(f"removed derived artifact: {path}")
    if not removed:
        print("No derived intermediate artifacts found.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely scan and export DVD-Video rips.")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_p = sub.add_parser("scan", help="Scan paths for VIDEO_TS DVD exports")
    scan_p.add_argument("paths", nargs="+")
    scan_p.add_argument("--title-set", default="auto", help="DVD title set to inspect, e.g. VTS_01, or auto for the largest set")
    scan_p.set_defaults(func=scan)

    wizard_p = sub.add_parser("wizard", help="Ask for export configuration interactively")
    wizard_p.add_argument("input", nargs="?")
    wizard_p.add_argument("--output-dir")
    wizard_p.add_argument("--title")
    wizard_p.add_argument("--output-format", choices=list(FORMAT_PRESETS), default="hevc-mp4")
    wizard_p.add_argument("--audio-mode", choices=["dual-mono", "preserve"], default="dual-mono")
    wizard_p.add_argument("--volume", type=float, default=1.18)
    wizard_p.add_argument("--title-set", default="auto")
    wizard_p.add_argument("--deinterlace", choices=["auto", "always", "never"], default="auto")
    wizard_p.add_argument("--field-order", choices=["auto", "bff", "tff"], default="auto")
    wizard_p.add_argument("--regenerate-timestamps", action=argparse.BooleanOptionalAction, default=True)
    wizard_p.add_argument("--video-bitrate", default="4500k")
    wizard_p.add_argument("--maxrate", default="6500k")
    wizard_p.add_argument("--bufsize", default="9000k")
    wizard_p.add_argument("--extra-video-args", default="")
    wizard_p.add_argument("--audio-encoder", default="aac")
    wizard_p.add_argument("--audio-bitrate", default="192k")
    wizard_p.add_argument("--extra-audio-args", default="")
    wizard_p.add_argument("--extra-output-args", default="")
    wizard_p.add_argument("--encoder", default="auto")
    wizard_p.add_argument("--max-warnings", type=int, default=10)
    wizard_p.add_argument("--stats-period", type=float, default=30)
    wizard_p.add_argument("--samples", nargs="*", default=list(DEFAULT_SAMPLES))
    wizard_p.add_argument("--sample-duration", type=int, default=60)
    wizard_p.add_argument("--min-size-mb", type=int, default=10)
    wizard_p.add_argument("--duration-tolerance-seconds", type=float, default=5.0)
    wizard_p.add_argument("--duration-tolerance-ratio", type=float, default=0.02)
    wizard_p.add_argument("--balance-tolerance-db", type=float, default=0.25)
    wizard_p.add_argument("--clipping-peak-db", type=float, default=-0.1)
    wizard_p.set_defaults(func=wizard)

    export_p = sub.add_parser("export", help="Export a DVD folder to a validated MP4 or MKV")
    export_p.add_argument("input")
    export_p.add_argument("--output-dir", required=True)
    export_p.add_argument("--title")
    export_p.add_argument("--output-format", choices=list(FORMAT_PRESETS), default="hevc-mp4")
    export_p.add_argument("--audio-mode", choices=["dual-mono", "preserve"], default="dual-mono")
    export_p.add_argument("--volume", type=float, default=1.18)
    export_p.add_argument("--title-set", default="auto")
    export_p.add_argument("--deinterlace", choices=["auto", "always", "never"], default="auto")
    export_p.add_argument("--field-order", choices=["auto", "bff", "tff"], default="auto")
    export_p.add_argument("--regenerate-timestamps", action=argparse.BooleanOptionalAction, default=True)
    export_p.add_argument("--video-bitrate", default="4500k")
    export_p.add_argument("--maxrate", default="6500k")
    export_p.add_argument("--bufsize", default="9000k")
    export_p.add_argument("--extra-video-args", default="")
    export_p.add_argument("--audio-encoder", default="aac")
    export_p.add_argument("--audio-bitrate", default="192k")
    export_p.add_argument("--extra-audio-args", default="")
    export_p.add_argument("--extra-output-args", default="")
    export_p.add_argument("--encoder", default="auto")
    export_p.add_argument("--max-warnings", type=int, default=10)
    export_p.add_argument("--stats-period", type=float, default=30)
    export_p.add_argument("--samples", nargs="*", default=list(DEFAULT_SAMPLES))
    export_p.add_argument("--sample-duration", type=int, default=60)
    export_p.add_argument("--min-size-mb", type=int, default=10)
    export_p.add_argument("--duration-tolerance-seconds", type=float, default=5.0)
    export_p.add_argument("--duration-tolerance-ratio", type=float, default=0.02)
    export_p.add_argument("--balance-tolerance-db", type=float, default=0.25)
    export_p.add_argument("--clipping-peak-db", type=float, default=-0.1)
    export_p.add_argument("--dry-run", action="store_true")
    export_p.add_argument("--overwrite", action="store_true")
    export_p.add_argument("--keep-intermediates", action="store_true")
    export_p.add_argument("--allow-output-inside-source", action="store_true")
    export_p.set_defaults(func=export)

    clean_p = sub.add_parser("clean", help="Remove derived intermediate files from an output directory")
    clean_p.add_argument("output_dir")
    clean_p.set_defaults(func=clean)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
