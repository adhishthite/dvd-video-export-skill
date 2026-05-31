---
name: dvd-video-export
description: Convert DVD-Video rips and VIDEO_TS folders into validated MP4 or MKV exports. Use when Codex needs to scan backup folders for DVD exports, inspect VOB/IFO/BUP structures, create a single watchable H.265 or H.264 export, preserve originals read-only, handle split VOB/DVD timestamp quirks, convert interview/speech audio to centered dual-mono, boost audio to an acceptable level, report progress/ETA, and validate duration, codec, size, and audio balance.
---

# DVD Video Export

## Core Rules

- Treat every source DVD folder as read-only. Never modify or delete `.VOB`, `.IFO`, `.BUP`, `VIDEO_TS`, or backup-source files.
- Write exports only to a separate output folder, preferably outside the source tree.
- Before encoding, scan and summarize DVD candidates, sizes, streams, durations, and output plan.
- For interviews, talks, satsangs, lectures, and speech-first DVD rips, use dual-mono audio by default: mix left and right evenly into both speakers.
- Apply a modest audio boost by default, usually `1.15` to `1.20`; verify peaks remain below clipping.
- Ask for the output format. Prefer `hevc-mp4` for compact Apple-friendly exports, but support `h264-mp4`, `hevc-mkv`, and `h264-mkv` when the user wants broader compatibility or a different container.
- Validate outputs before claiming success: duration, size, video codec, aspect ratio, audio codec, channel count, and sampled channel balance.

## Recommended Workflow

1. Locate DVD exports:

```bash
python3 ~/.codex/skills/dvd-video-export/scripts/export_dvd_video.py scan /path/to/backup
```

2. Ask the user for export knobs before encoding. Cover at least:

- source DVD folder;
- output folder;
- title/output filename;
- audio mode (`dual-mono` recommended for interviews);
- volume boost (`1.18` recommended unless the user asks otherwise);
- output format preset (`hevc-mp4` recommended by default; also offer `h264-mp4`, `hevc-mkv`, `h264-mkv`);
- video encoder/quality (`auto`, `hevc_videotoolbox`, `h264_videotoolbox`, `libx265`, `libx264`; `4500k` default for old DVD sources);
- DVD title set (`auto` chooses the largest title set; specify `VTS_01`, `VTS_02`, etc. when needed);
- deinterlace mode, field order, and timestamp regeneration;
- maximum tolerated ffmpeg warning/error lines;
- whether to dry-run first;
- whether to keep or clean derived intermediates;
- overwrite behavior.

Use the interactive wizard when the user wants the script to ask:

```bash
python3 ~/.codex/skills/dvd-video-export/scripts/export_dvd_video.py wizard
```

3. Dry-run a candidate before writing video:

```bash
python3 ~/.codex/skills/dvd-video-export/scripts/export_dvd_video.py export \
  "/path/to/DVD title folder" \
  --output-dir "/path/to/export folder" \
  --dry-run
```

4. Encode the export:

```bash
python3 ~/.codex/skills/dvd-video-export/scripts/export_dvd_video.py export \
  "/path/to/DVD title folder" \
  --output-dir "/path/to/export folder" \
  --title "Readable Title"
```

5. If the user is happy with the final file, clean only derived intermediates:

```bash
python3 ~/.codex/skills/dvd-video-export/scripts/export_dvd_video.py clean \
  "/path/to/export folder"
```

## Script Defaults

The bundled script:

- detects one or more `VIDEO_TS` folders under the input;
- provides an interactive `wizard` command that asks for config knobs and requires explicit confirmation before a real export;
- groups each `VIDEO_TS` parent as a disc or part;
- groups VOBs by DVD title set and defaults to the largest title set unless a specific `--title-set` is provided;
- rejects paths with concat-unsafe characters such as `|` or newlines;
- uses byte-concat (`concat:file1|file2`) per disc to avoid split-VOB packet problems;
- encodes each disc to a temporary derived file using the selected output format;
- joins derived parts into one final MP4 or MKV using stream copy;
- copies video during audio-only rework when possible;
- converts audio to centered dual-mono with channel-aware filters: duplicate mono, average stereo, and use center-weighted mixing for multi-channel audio;
- boosts audio with `volume=<boost>` after dual-mono;
- refuses to write output inside the source tree or discovered DVD root unless `--allow-output-inside-source` is explicitly supplied;
- refuses to overwrite output unless `--overwrite` is supplied;
- fails when ffmpeg warning/error lines exceed `--max-warnings`;
- reports progress from ffmpeg `time=`, `speed=`, output size, percent, elapsed, and ETA;
- hard-fails validation on missing/wrong streams, duration mismatch, tiny output size, unavailable audio stats, imbalanced dual-mono samples, or near-clipping peaks.

Default settings are `--output-format hevc-mp4`, `--audio-mode dual-mono`, `--volume 1.18`, `--title-set auto`, `--deinterlace auto`, `--regenerate-timestamps`, `--video-bitrate 4500k`, `--audio-bitrate 192k`, `--encoder auto`, and `--max-warnings 10`.

## Guardrails

- Do not use shell loops that split on spaces for DVD paths. Use null-safe `find -print0`, Python `Path`, or the bundled script.
- Do not concatenate multiple discs with the concat demuxer in one pass when DVD timestamps look suspicious. Encode per disc, then join derived MP4 parts.
- Do not delete intermediate files until the final output is validated and the user confirms cleanup, unless the script is cleaning its own failed partial output in the export folder.
- If ffmpeg reports warning/error lines beyond the configured threshold, stop and inspect with a short probe. Try per-disc byte-concat before using a global VOB list.
- If duration is unexpectedly short, stop and re-plan. DVD metadata and timestamps can mislead ffmpeg.
- If multiple title sets exist, verify the selected title set is the intended program material before running a long encode.
- If the user asks to delete anything, confirm it is a derived export artifact and not the original DVD backup.
- If using the non-interactive `export` command and the user has not already specified config choices, pause and ask before running the real encode.

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
python3 -m unittest discover -s ~/.codex/skills/dvd-video-export/tests -v
```

## Reference

Read `references/dvd-export-notes.md` when debugging timestamps, audio imbalance, or DVD grouping behavior.
