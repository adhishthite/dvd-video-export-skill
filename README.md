# DVD Video Export Skill

[![skills.sh](https://skills.sh/b/adhishthite/dvd-video-export-skill)](https://skills.sh/adhishthite/dvd-video-export-skill)

Agent skill for converting DVD-Video rips (`VIDEO_TS`, `.VOB`, `.IFO`, `.BUP`) into validated H.265 MP4 exports with safe source handling and speech-friendly audio defaults.

## What it does

- Scans backup folders for DVD-Video exports.
- Encodes DVD rips into H.265/HEVC MP4 files.
- Treats original DVD/source files as read-only.
- Writes outputs only to a separate export folder.
- Encodes per DVD disc/part, then joins derived MP4 parts.
- Converts interview/speech audio to centered dual-mono by default.
- Applies a modest audio boost by default.
- Validates duration, streams, codecs, size, audio balance, and clipping risk.
- Includes an interactive wizard that asks for configuration knobs before a real encode.

## Install

Install with the Vercel `skills` CLI:

```bash
npx skills add adhishthite/dvd-video-export-skill --skill dvd-video-export -a codex
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

Scan for DVD exports:

```bash
python3 ~/.codex/skills/dvd-video-export/scripts/export_dvd_video.py scan /path/to/backup
```

Run the interactive wizard:

```bash
python3 ~/.codex/skills/dvd-video-export/scripts/export_dvd_video.py wizard
```

Dry-run a candidate:

```bash
python3 ~/.codex/skills/dvd-video-export/scripts/export_dvd_video.py export \
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
python3 -m unittest discover -s skills/dvd-video-export/tests -v
```

The tests cover DVD discovery, path guardrails, dry-run behavior, title-set selection, ffmpeg warning failure, audio filters, validation failure paths, and cleanup behavior.

## License

MIT
