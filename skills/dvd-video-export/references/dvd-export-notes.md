# DVD export notes

## Lessons from real backup DVD rips

- DVD backups usually contain `VIDEO_TS` folders with `.VOB`, `.IFO`, and `.BUP` files. The `.IFO/.BUP` files are metadata and menu/navigation data; the main program is usually `VTS_01_1.VOB`, `VTS_01_2.VOB`, etc.
- Split VOB files may have discontinuous timestamps or corrupt packets at boundaries. A short probe can look clean while a full cross-disc concat produces audio errors.
- Byte-concat per disc is safer than a concat demuxer list across all discs. Encode each disc/part first, then join the derived MP4 parts with `-c copy`.
- Do not assume every DVD is PAL. Probe frame rate and field order before deinterlacing or regenerating timestamps. PAL interview material is commonly `720x576`, `25 fps`, `4:3`, bottom-field-first interlaced MPEG-2, but NTSC and progressive DVDs need different handling.
- DVDs may contain multiple title sets (`VTS_01`, `VTS_02`, etc.). The longest title set is often the main program, but extras and menus can exist. Confirm the selected title set when a disc has more than one.
- For interviews and speech-first recordings, original stereo may be unbalanced. Dual-mono is usually better than preserving misleading stereo separation.
- For 5.1 or other multi-channel speech recordings, center channel content is often important. Use a center-aware dual-mono mix rather than only averaging front-left and front-right.
- A modest post-mix volume boost such as `volume=1.18` raises perceived loudness without aggressive normalization. Always sample peaks afterward.
- Hardware H.265 (`hevc_videotoolbox`) is much faster than `libx265`. For old DVD sources, a high hardware bitrate such as `4500k` is a practical archive/watchable tradeoff. Use H.264 presets when device compatibility matters more than file size, and MKV presets when the user wants a non-MP4 container.

## Expected validation evidence

For each final export, report:

- final path;
- duration;
- file size;
- video codec, dimensions, display aspect ratio;
- audio codec, channel layout, bitrate;
- whether audio is balanced from at least three samples;
- whether original source files were untouched.
