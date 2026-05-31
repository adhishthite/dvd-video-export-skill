import argparse
import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "export_dvd_video.py"
SPEC = importlib.util.spec_from_file_location("export_dvd_video", SCRIPT_PATH)
dvd = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = dvd
SPEC.loader.exec_module(dvd)


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


class DVDVideoExportTests(unittest.TestCase):
    def test_safe_title_removes_unsafe_filename_characters(self):
        self.assertEqual(dvd.safe_title('Bad:/Name* "Test"?'), "Bad Name Test")
        self.assertEqual(dvd.safe_title("   "), "DVD Export")

    def test_time_and_progress_parsing(self):
        self.assertEqual(dvd.fmt_time(12103.3), "3:21:43")
        self.assertEqual(dvd.fmt_time(None), "unknown")
        line = "frame=123 time=01:02:03.40 bitrate=0.0 speed=21.5x"
        self.assertAlmostEqual(dvd.parse_time(line), 3723.4)
        self.assertEqual(dvd.parse_speed(line), "21.5x")

    def test_main_vobs_excludes_menu_and_natural_sorts(self):
        with tempfile.TemporaryDirectory() as tmp:
            video_ts = Path(tmp) / "Title With Spaces" / "VIDEO_TS"
            for name in [
                "VIDEO_TS.IFO",
                "VTS_01_0.VOB",
                "VTS_01_10.VOB",
                "VTS_01_2.VOB",
                "VTS_01_1.VOB",
            ]:
                touch(video_ts / name)

            names = [path.name for path in dvd.main_vobs(video_ts)]
            self.assertEqual(names, ["VTS_01_1.VOB", "VTS_01_2.VOB", "VTS_01_10.VOB"])

    def test_discover_discs_handles_spaces_and_multiple_video_ts_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Backup Root"
            touch(root / "A Title" / "Disc 1" / "VIDEO_TS" / "VTS_01_1.VOB")
            touch(root / "A Title" / "Disc 2" / "VIDEO_TS" / "VTS_01_1.VOB")
            touch(root / "Not DVD" / "file.txt")

            discs = dvd.discover_discs(root)
            self.assertEqual(len(discs), 2)
            self.assertTrue(all(disc.root.name.startswith("Disc ") for disc in discs))

    def test_discover_discs_selects_largest_title_set_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            video_ts = Path(tmp) / "DVD" / "VIDEO_TS"
            small = video_ts / "VTS_01_1.VOB"
            large = video_ts / "VTS_02_1.VOB"
            touch(small)
            touch(large)
            large.write_bytes(b"x" * 10)

            discs = dvd.discover_discs(Path(tmp) / "DVD")

            self.assertEqual(discs[0].title_set, "VTS_02")
            self.assertEqual(discs[0].vobs, [large])

    def test_discover_discs_can_select_explicit_title_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            video_ts = Path(tmp) / "DVD" / "VIDEO_TS"
            touch(video_ts / "VTS_01_1.VOB")
            touch(video_ts / "VTS_02_1.VOB")

            discs = dvd.discover_discs(Path(tmp) / "DVD", "VTS_01")

            self.assertEqual(discs[0].title_set, "VTS_01")
            self.assertEqual(discs[0].vobs[0].name, "VTS_01_1.VOB")

    def test_unsafe_concat_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad|path.VOB"
            touch(path)

            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                dvd.concat_url([path])

    def test_ffconcat_quote_escapes_apostrophes(self):
        quoted = dvd.ffconcat_quote(Path("/tmp/O'Brien/part.mp4"))
        self.assertEqual(quoted, "'/tmp/O'\\''Brien/part.mp4'")

    def test_validate_paths_refuses_output_inside_source_before_creating_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            output = source / "exports"

            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                dvd.validate_paths(source, output, allow_inside=False, create_output=True)

            self.assertFalse(output.exists())

    def test_validate_paths_refuses_dvd_root_when_input_is_video_ts(self):
        with tempfile.TemporaryDirectory() as tmp:
            video_ts = Path(tmp) / "DVD" / "VIDEO_TS"
            touch(video_ts / "VTS_01_1.VOB")
            disc = dvd.discover_discs(video_ts)[0]

            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                dvd.validate_paths(video_ts, video_ts.parent, allow_inside=False, create_output=True, discs=[disc])

    def test_validate_paths_dry_run_does_not_create_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            output = Path(tmp) / "exports"
            source.mkdir()

            dvd.validate_paths(source, output, allow_inside=False, create_output=False)

            self.assertFalse(output.exists())

    def test_audio_filter_defaults_to_dual_mono_with_volume_boost(self):
        args = argparse.Namespace(audio_mode="dual-mono", volume=1.18)
        self.assertEqual(
            dvd.audio_filter(args),
            "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1,volume=1.18",
        )

    def test_audio_filter_can_preserve_stereo_without_boost(self):
        args = argparse.Namespace(audio_mode="preserve", volume=1.0)
        self.assertEqual(dvd.audio_filter(args), "anull")

    def test_audio_filter_handles_mono_and_multichannel_dual_mono(self):
        args = argparse.Namespace(audio_mode="dual-mono", volume=1.0)
        with mock.patch.object(dvd, "ffprobe_audio_info", return_value={"channels": 1}):
            self.assertEqual(dvd.audio_filter(args, Path("in.mp4")), "pan=stereo|c0=c0|c1=c0")
        with mock.patch.object(dvd, "ffprobe_audio_info", return_value={"channels": 6}):
            self.assertIn("0.5*c2", dvd.audio_filter(args, Path("in.mp4")))

    def test_video_filter_uses_probed_frame_rate_and_field_order(self):
        args = argparse.Namespace(deinterlace="auto", field_order="auto", regenerate_timestamps=True)
        disc = dvd.Disc(root=Path("/tmp/dvd"), video_ts=Path("/tmp/dvd/VIDEO_TS"), vobs=[Path("/tmp/dvd/VIDEO_TS/VTS_01_1.VOB")])
        with mock.patch.object(
            dvd,
            "ffprobe_video_info",
            return_value={"avg_frame_rate": "30000/1001", "field_order": "tt"},
        ):
            vf = dvd.video_filter_for_disc(disc, args)

        self.assertIn("parity=tff", vf)
        self.assertIn("29.970030", vf)

    def test_video_filter_does_not_deinterlace_progressive_source(self):
        args = argparse.Namespace(deinterlace="auto", field_order="auto", regenerate_timestamps=True)
        disc = dvd.Disc(root=Path("/tmp/dvd"), video_ts=Path("/tmp/dvd/VIDEO_TS"), vobs=[Path("/tmp/dvd/VIDEO_TS/VTS_01_1.VOB")])
        with mock.patch.object(
            dvd,
            "ffprobe_video_info",
            return_value={"avg_frame_rate": "25/1", "field_order": "progressive"},
        ):
            vf = dvd.video_filter_for_disc(disc, args)

        self.assertNotIn("bwdif", vf)
        self.assertIn("setpts", vf)

    def test_stream_ffmpeg_fails_when_warning_threshold_exceeded(self):
        class FakeProc:
            def __init__(self):
                self.stdout = iter(["Invalid frame dimensions 0x0\n", "time=00:00:01.00 speed=1x\n"])

            def poll(self):
                return None

            def wait(self):
                return 0

        with mock.patch.object(dvd.subprocess, "Popen", return_value=FakeProc()):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                dvd.stream_ffmpeg(["ffmpeg"], total_duration=10, label="test", max_warnings=0)

    def test_validate_output_fails_on_imbalanced_dual_mono(self):
        summary = {
            "format": {"duration": "100", "size": str(20 * 1024 * 1024)},
            "streams": [
                {"codec_type": "video", "codec_name": "hevc", "width": 720, "height": 576},
                {"codec_type": "audio", "codec_name": "aac", "channels": 2},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out.mp4"
            touch(output)
            with mock.patch.object(dvd, "ffprobe_summary", return_value=summary), mock.patch.object(
                dvd, "astats_sample", return_value=(-20.0, -25.0, -3.0, -3.0)
            ):
                with redirect_stdout(io.StringIO()), self.assertRaises(dvd.ValidationError):
                    dvd.validate_output(output, ("00:00:00",), expected_duration=100, expect_balanced=True)

    def test_validate_output_fails_on_duration_mismatch(self):
        summary = {
            "format": {"duration": "80", "size": str(20 * 1024 * 1024)},
            "streams": [
                {"codec_type": "video", "codec_name": "hevc", "width": 720, "height": 576},
                {"codec_type": "audio", "codec_name": "aac", "channels": 2},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out.mp4"
            touch(output)
            with mock.patch.object(dvd, "ffprobe_summary", return_value=summary), mock.patch.object(
                dvd, "astats_sample", return_value=(-20.0, -20.0, -3.0, -3.0)
            ):
                with redirect_stdout(io.StringIO()), self.assertRaises(dvd.ValidationError):
                    dvd.validate_output(output, ("00:00:00",), expected_duration=100, expect_balanced=True)

    def test_export_dry_run_writes_no_files_and_discovers_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "DVD Source"
            output = Path(tmp) / "Export Out"
            touch(source / "Disc 1" / "VIDEO_TS" / "VTS_01_1.VOB")
            touch(source / "Disc 2" / "VIDEO_TS" / "VTS_01_1.VOB")
            args = argparse.Namespace(
                input=str(source),
                output_dir=str(output),
                title="My Export",
                audio_mode="dual-mono",
                volume=1.18,
                title_set="auto",
                deinterlace="auto",
                field_order="auto",
                regenerate_timestamps=True,
                video_bitrate="4500k",
                maxrate="6500k",
                bufsize="9000k",
                audio_bitrate="192k",
                encoder="hevc_videotoolbox",
                max_warnings=10,
                stats_period=30,
                samples=list(dvd.DEFAULT_SAMPLES),
                dry_run=True,
                overwrite=False,
                keep_intermediates=False,
                allow_output_inside_source=False,
            )

            with mock.patch.object(dvd, "require_tools"), mock.patch.object(
                dvd, "ffprobe_duration", return_value=60.0
            ):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    dvd.export(args)

            text = buffer.getvalue()
            self.assertIn("Discs/parts: 2", text)
            self.assertIn("Dry run only; no files written.", text)
            self.assertFalse(output.exists())

    def test_clean_removes_only_known_derived_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            keep = output / "final.mp4"
            artifacts = [
                output / ".Title.part-01.mp4",
                output / ".Title.joined.mp4",
                output / ".Title.joined.parts.txt",
                output / "vob-concat-list.txt",
            ]
            touch(keep)
            for artifact in artifacts:
                touch(artifact)

            with redirect_stdout(io.StringIO()):
                dvd.clean(argparse.Namespace(output_dir=str(output)))

            self.assertTrue(keep.exists())
            self.assertTrue(all(not artifact.exists() for artifact in artifacts))


if __name__ == "__main__":
    unittest.main()
