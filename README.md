# DVD Video Export Skill

Agent skill for converting DVD-Video rips (`VIDEO_TS`, `.VOB`, `.IFO`, `.BUP`) into validated MP4 or MKV exports with safe source handling and speech-friendly audio defaults.

## What it does

- Scans backup folders for DVD-Video exports.
- Encodes DVD rips into user-selected output presets: `hevc-mp4`, `h264-mp4`, `hevc-mkv`, or `h264-mkv`.
- Treats original DVD/source files as read-only.
- Writes outputs only to a separate export folder.
- Encodes per DVD disc/part, then joins derived parts.
- Converts interview/speech audio to centered dual-mono by default.
- Applies a modest audio boost by default.
- Validates duration, streams, selected video codec, size, audio balance, and clipping risk.
- Keeps the user in control: the agent should inspect with tools, summarize findings, ask before installs/encodes/cleanup, and then use the script as a helper.
- Includes an interactive wizard for cases where the user wants the script to ask for configuration knobs directly.

## Install

Install with the Vercel `skills` CLI:

```bash
npx skills add adhishthite/dvd-video-export-skill --skill dvd-video-export -a codex
```

The skills.sh badge can be added after the repository is indexed by skills.sh:

```md
[![skills.sh](https://skills.sh/b/adhishthite/dvd-video-export-skill)](https://skills.sh/adhishthite/dvd-video-export-skill)
```

Or install directly from the skill path:

```bash
npx skills add https://github.com/adhishthite/dvd-video-export-skill/tree/main/skills/dvd-video-export -a codex
```

## Requirements

- Python 3.10+
- `ffmpeg`
- `ffprobe`

On macOS with Homebrew:

```bash
brew install ffmpeg
```

## Usage

Check local dependencies:

```bash
for tool in uv ffmpeg ffprobe; do command -v "$tool" >/dev/null || echo "missing:$tool"; done
```

Scan for DVD exports:

```bash
uv run ~/.codex/skills/dvd-video-export/scripts/export_dvd_video.py scan /path/to/backup
```

Run the interactive wizard:

```bash
uv run ~/.codex/skills/dvd-video-export/scripts/export_dvd_video.py wizard
```

Dry-run a candidate:

```bash
uv run ~/.codex/skills/dvd-video-export/scripts/export_dvd_video.py export \
  "/path/to/DVD title folder" \
  --output-dir "/path/to/export folder" \
  --dry-run
```

## Safety notes

This skill includes scripts that run `ffmpeg` over user-selected media files. Review the script before use.

The export helper is designed to fail closed:

- It never intentionally modifies or deletes source `.VOB`, `.IFO`, `.BUP`, or `VIDEO_TS` files.
- It refuses to write outputs inside the source tree or discovered DVD root by default.
- Dry-runs do not create the output directory.
- It refuses concat-unsafe source paths containing `|` or newlines.
- It refuses to overwrite existing outputs unless `--overwrite` is provided.
- It keeps derived intermediates if validation fails.
- Cleanup only targets known derived intermediate artifacts.

## Tests

Run the unit tests:

```bash
uv run -m unittest discover -s skills/dvd-video-export/tests -v
```

The tests cover DVD discovery, path guardrails, dry-run behavior, title-set selection, format presets, ffmpeg warning failure, audio filters, validation thresholds, validation failure paths, and cleanup behavior.

## License

MIT
