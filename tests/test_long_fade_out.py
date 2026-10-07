"""Optional local native fixtures for the verified UInt24 fade-out reader."""

from pathlib import Path
import tempfile
import unittest

from pt_api import ProToolsSession, TimecodeEngine


FIXTURES = Path(__file__).parent / "fixtures"
BEFORE = FIXTURES / "long_fade_before_25fps.ptx"
AFTER_25 = FIXTURES / "long_fade_out_25fps.ptx"
AFTER_23976 = FIXTURES / "long_fade_out_23976fps.ptx"
RESAVED_25 = FIXTURES / "long_fade_out_25fps_pt_resaved.ptx"
RESAVED_23976 = FIXTURES / "long_fade_out_23976fps_pt_resaved.ptx"


class NativeLongFadeOutTests(unittest.TestCase):
    @unittest.skipUnless(RESAVED_25.is_file(), "Local Pro Tools-resaved 25-fps fade fixture absent.")
    def test_25fps_fade_after_pro_tools_save_round_trip(self):
        self.assert_native_round_trip(RESAVED_25, 0x02, 1_728_000_000, 96_192)

    @unittest.skipUnless(RESAVED_23976.is_file(), "Local Pro Tools-resaved 23.976-fps fade fixture absent.")
    def test_23976fps_fade_after_pro_tools_save_round_trip(self):
        self.assert_native_round_trip(RESAVED_23976, 0x09, 1_729_728_000, 96_096)

    @unittest.skipUnless(BEFORE.is_file() and AFTER_25.is_file(), "Local native 25-fps fade fixtures absent.")
    def test_25fps_pair_read_and_repeated_noop_save(self):
        before = ProToolsSession(BEFORE)
        after = ProToolsSession(AFTER_25)
        self.assertEqual(before.get_timeline_clips(), after.get_timeline_clips(False))
        self.assert_native_round_trip(AFTER_25, 0x02, 1_728_000_000, 96_192)

    @unittest.skipUnless(AFTER_23976.is_file(), "Local native 23.976-fps fade fixture absent.")
    def test_23976fps_read_and_repeated_noop_save(self):
        self.assert_native_round_trip(AFTER_23976, 0x09, 1_729_728_000, 96_096)

    def assert_native_round_trip(self, path, rate, clip_start, fade_duration):
        original = path.read_bytes()
        session = ProToolsSession(path)
        self.assertEqual(session.sample_rate, 48_000)
        self.assertEqual(session.frame_rate_enum, rate)
        self.assertEqual(len(session.get_tracks()), 1)
        engine = TimecodeEngine(session.sample_rate, session.frame_rate_enum)
        timeline = session.get_timeline_clips()
        self.assertEqual(len(timeline), 2)
        audio = next(item for item in timeline if not item["is_fade"])
        fade = next(item for item in timeline if item["is_fade"])
        self.assertEqual(audio["start_samples"], clip_start)
        self.assertEqual(audio["length_samples"], 192_192)
        self.assertEqual(fade["length_samples"], fade_duration)
        self.assertEqual(fade["start_samples"], clip_start + 192_192 - fade_duration)
        self.assertEqual(fade["end_samples"], audio["end_samples"])
        self.assertEqual(fade["clip_name"], audio["clip_name"])
        self.assertEqual(engine.samples_to_timecode(fade["start_samples"]), "10:00:02:00")
        self.assertEqual(engine.samples_to_timecode(fade["end_samples"]), "10:00:04:00")
        self.assertEqual(engine.samples_to_timecode(fade_duration), "00:00:02:00")
        geometry_root = session._root_blocks(0x2630)[0]
        payload = bytes(geometry_root.get_all_blocks(0x262f)[0].items[0])
        self.assertEqual(len(payload), 29)
        self.assertEqual(payload[5], 0x33)
        self.assertEqual(payload[8:11], payload[11:14])
        self.assertEqual(int.from_bytes(payload[8:11], "little"), fade_duration)
        self.assertEqual(payload[14], 0x02)

        before_tree = [root.to_bytes()[0] for root in session.root_items]
        operations = (
            lambda: session.move_clip(audio["clip_name"], 10, 0, 5, 0),
            lambda: session.duplicate_clip(audio["clip_name"], 10, 0, 5, 0),
            lambda: session.trim_clip_start(audio["clip_name"], 100),
            lambda: session.trim_clip_end(audio["clip_name"], 100),
        )
        for operation in operations:
            with self.assertRaisesRegex(NotImplementedError, "attached fades"):
                operation()
            self.assertEqual([root.to_bytes()[0] for root in session.root_items], before_tree)

        with tempfile.TemporaryDirectory() as directory:
            for index in range(2):
                output = Path(directory) / ("noop_%d.ptx" % index)
                session.save(output)
                self.assertEqual(output.read_bytes(), original)
                session = ProToolsSession(output)
                self.assertEqual(session.get_timeline_clips(), timeline)
                reloaded_geometry = session._root_blocks(0x2630)[0].get_all_blocks(0x262f)[0]
                self.assertEqual(bytes(reloaded_geometry.items[0]), payload)
        self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
