---
name: dvd-video-export
description: Convert DVD-Video rips and VIDEO_TS folders into validated MP4 or MKV exports. Use when Codex needs to scan backup folders for DVD exports, inspect VOB/IFO/BUP structures, create a single watchable H.265 or H.264 export, preserve originals read-only, handle split VOB/DVD timestamp quirks, convert interview/speech audio to centered dual-mono, boost audio to an acceptable level, report progress/ETA, and validate duration, codec, size, and audio balance.
---

# DVD Video Export

## Core Rules

- The user is the control point. Use tools to inspect, report concrete findings, and ask before installing dependencies, starting long encodes, overwriting outputs, cleaning intermediates, or changing safety limits.
- Treat every source DVD folder as read-only. Never modify or delete `.VOB`, `.IFO`, `.BUP`, `VIDEO_TS`, or backup-source files.
- Write exports only to a separate output folder, preferably outside the source tree.
- Before encoding, scan and summarize DVD candidates, sizes, streams, durations, and output plan.
- For interviews, talks, satsangs, lectures, and speech-first DVD rips, use dual-mono audio by default: mix left and right evenly into both speakers.
- Apply a modest audio boost by default, usually `1.15` to `1.20`; verify peaks remain below clipping.
- Ask for the output format. Prefer `hevc-mp4` for compact Apple-friendly exports, but support `h264-mp4`, `hevc-mkv`, and `h264-mkv` when the user wants broader compatibility or a different container.
- Validate outputs before claiming success: duration, size, video codec, aspect ratio, audio codec, channel count, and sampled channel balance.

## User-Centered Flow

- First inspect. Do not assume dependencies, source structure, durations, audio balance, or intended output format.
- Then report the relevant facts in plain language: what was found, what is missing, estimated duration/size when available, and what choices matter.
- Use a structured ask-user tool when the current agent mode exposes one. If no such tool is available, ask the same choices directly in chat and wait. Do not silently replace a multi-choice decision with a default.
- Ask the user for decisions at control points: dependency install vs stop, format/container, audio handling, quality/size tradeoff, output folder, dry-run vs real export, and cleanup.
- Prefer safe recommendations, but present them as defaults the user can override.
- Never bury a destructive or expensive action inside an automatic script prompt. Ask in chat before doing it.
- During long work, provide progress, percent, speed, ETA, and current output path.

## Recommended Workflow

1. Check dependencies with tools before scanning or exporting. Because `uv` itself may be missing, start with the shell rather than the Python helper:

```bash
for tool in uv ffmpeg ffprobe; do command -v "$tool" >/dev/null || echo "missing:$tool"; done
```

If any tool is missing, tell the user exactly what is missing and ask whether they want to install the missing tools or stop. Do not install automatically from the skill. If the user approves installation and Homebrew is available, install the reported packages, usually:

```bash
brew install uv ffmpeg
```

`ffprobe` is included with the Homebrew `ffmpeg` package.

After `uv` is available, the helper can also report dependency status as JSON:

```bash
uv run ~/.codex/skills/dvd-video-export/scripts/export_dvd_video.py doctor --json
```

2. Locate DVD exports:

```bash
uv run ~/.codex/skills/dvd-video-export/scripts/export_dvd_video.py scan /path/to/backup
```

The scan reports suggested export groups and every discovered DVD title set. If a folder contains date folders with numbered parts, such as `4th Jan/1/VIDEO_TS` and `4th Jan/2/VIDEO_TS`, treat each date as its own candidate final video unless the user says otherwise.

3. Ask the user for export knobs before encoding. Use the scan/probe results to recommend defaults, but let the user change every meaningful knob:

- source DVD folder;
- export grouping strategy: entire input as one video, one video per date/folder group, or selected groups only;
- ordering for grouped parts, especially numbered folders like `1`, `2`, `3`;
- output folder;
- title/output filename;
- audio mode (`dual-mono` recommended for interviews);
- volume boost (`1.18` recommended unless the user asks otherwise);
- output format preset (`hevc-mp4` recommended by default; also offer `h264-mp4`, `hevc-mkv`, `h264-mkv`);
- video encoder, bitrate, maxrate, buffer size, and optional extra ffmpeg video/output args;
- audio mode, volume, encoder, bitrate, and optional extra ffmpeg audio args;
- DVD title-set strategy (`all` combines every `VTS_*` title set in order, `auto` chooses the largest title set, or specify `VTS_01`, `VTS_02`, etc.);
- deinterlace mode, field order, and timestamp regeneration;
- validation sample times, sample duration, minimum output size, duration tolerance, dual-mono balance tolerance, and clipping threshold; if requested samples exceed output duration, the helper auto-generates safe in-range samples;
- progress update interval and maximum tolerated ffmpeg warning/error lines;
- whether to dry-run first;
- whether to keep or clean derived intermediates;
- overwrite behavior;
- whether to allow output inside the source tree, which should normally remain `no`.

Use the interactive wizard when the user wants the script to ask:

```bash
uv run ~/.codex/skills/dvd-video-export/scripts/export_dvd_video.py wizard
```

4. Dry-run a candidate before writing video:

```bash
uv run ~/.codex/skills/dvd-video-export/scripts/export_dvd_video.py export \
  "/path/to/DVD title folder" \
  --output-dir "/path/to/export folder" \
  --dry-run
```

5. Encode the export:

```bash
uv run ~/.codex/skills/dvd-video-export/scripts/export_dvd_video.py export \
  "/path/to/DVD title folder" \
  --output-dir "/path/to/export folder" \
  --title "Readable Title"
```

6. If the user is happy with the final file, clean only derived intermediates:

```bash
uv run ~/.codex/skills/dvd-video-export/scripts/export_dvd_video.py clean \
  "/path/to/export folder"
```

## Script Defaults

The bundled script:

- detects one or more `VIDEO_TS` folders under the input;
- provides a `doctor --json` command for structured dependency status after `uv` is available;
- provides an interactive `wizard` command that asks for config knobs and requires explicit confirmation before a real export;
- reports suggested export groups for parent folders such as date folders containing numbered DVD parts;
- groups each `VIDEO_TS` parent as a disc or part;
- groups VOBs by DVD title set, supports `--title-set all` to combine every title set in order, and supports explicit `VTS_01`/`VTS_02` selection;
- rejects paths with concat-unsafe characters such as `|` or newlines;
- uses byte-concat (`concat:file1|file2`) per disc to avoid split-VOB packet problems;
- encodes each disc to a temporary derived file using the selected output format;
- joins derived parts into one final MP4 or MKV using stream copy;
- copies video during audio-only rework when possible;
- converts audio to centered dual-mono with channel-aware filters: duplicate mono, average stereo, and use center-weighted mixing for multi-channel audio;
- boosts audio with `volume=<boost>` after dual-mono;
- writes a derived `.job.json` manifest in the output folder for resumability/audit during long exports;
- lets the user tune output format, title-set strategy, video encoder, video rate control, audio encoder, audio bitrate, ffmpeg extra args, progress cadence, validation samples, validation thresholds, dry-run, overwrite, and intermediate cleanup from the wizard;
- refuses to write output inside the source tree or discovered DVD root unless `--allow-output-inside-source` is explicitly supplied;
- refuses to overwrite output unless `--overwrite` is supplied;
- fails when ffmpeg warning/error lines exceed `--max-warnings`;
- reports progress from ffmpeg `time=`, `speed=`, output size, percent, elapsed, and ETA;
- hard-fails validation on missing/wrong streams, duration mismatch, tiny output size, unavailable audio stats, imbalanced dual-mono samples, or near-clipping peaks.

Default settings are `--output-format hevc-mp4`, `--audio-mode dual-mono`, `--volume 1.18`, `--title-set auto`, `--deinterlace auto`, `--regenerate-timestamps`, `--video-bitrate 4500k`, `--maxrate 6500k`, `--bufsize 9000k`, `--audio-encoder aac`, `--audio-bitrate 192k`, `--encoder auto`, `--samples 00:05:00 01:30:00 03:00:00`, `--sample-duration 60`, `--min-size-mb 10`, `--duration-tolerance-seconds 5`, `--duration-tolerance-ratio 0.02`, `--balance-tolerance-db 0.25`, `--clipping-peak-db -0.1`, `--stats-period 30`, and `--max-warnings 10`. For multi-title-set discs, the wizard recommends `all` unless the user selects a specific title or largest-title-only export.

## Guardrails

- Do not use shell loops that split on spaces for DVD paths. Use null-safe `find -print0`, Python `Path`, or the bundled script.
- Do not concatenate multiple discs with the concat demuxer in one pass when DVD timestamps look suspicious. Encode per disc, then join derived parts.
- Do not delete intermediate files until the final output is validated and the user confirms cleanup, unless the script is cleaning its own failed partial output in the export folder.
- If ffmpeg reports warning/error lines beyond the configured threshold, stop and inspect with a short probe. Try per-disc byte-concat before using a global VOB list.
- If duration is unexpectedly short, stop and re-plan. DVD metadata and timestamps can mislead ffmpeg.
- If multiple title sets exist, do not hide them behind `auto`. Present `all`, `auto`, and explicit title-set choices before running a long encode.
- If a parent folder contains multiple dates or numbered parts, ask whether the target is one final video per date/folder group, one final video for the whole parent, or only selected groups.
- If the user asks to delete anything, confirm it is a derived export artifact and not the original DVD backup.
- If using the non-interactive `export` command and the user has not already specified config choices, pause and ask before running the real encode.
- Dependency install is a skill/agent decision: inspect with shell tools first, ask the user, then run an install command only after approval. Do not rely on unattended dependency installation.

## Useful Checks

List DVD structures:

```bash
find /path/to/backup -type d -name VIDEO_TS -print
```

Check final streams:

```bash
ffprobe -v error -show_entries format=duration,size,bit_rate \
  -show_entries stream=index,codec_type,codec_name,width,height,display_aspect_ratio,channels,channel_layout,bit_rate \
  -of default=noprint_wrappers=1 "/path/to/final-output.mp4"
```

Check channel balance samples:

```bash
ffmpeg -hide_banner -nostdin -ss 00:05:00 -t 60 -i "/path/to/final-output.mp4" \
  -af astats=metadata=1:reset=0 -f null -
```

Run the unit tests after editing the script:

```bash
uv run -m unittest discover -s ~/.codex/skills/dvd-video-export/tests -v
```

## Reference

Read `references/dvd-export-notes.md` when debugging timestamps, audio imbalance, or DVD grouping behavior.
