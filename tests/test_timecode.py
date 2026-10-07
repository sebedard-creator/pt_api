import os
import struct
import tempfile
import unittest

from pt_api import ProToolsSession, TimecodeEngine


NATIVE_25FPS_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "native_25fps.ptx"
)
NATIVE_25FPS_RESAVED_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "native_25fps_pt_resaved.ptx"
)


class TimecodeEngineTests(unittest.TestCase):
    def test_25fps_exact_positions_and_durations(self):
        engine = TimecodeEngine(48_000, 0x02)
        self.assertEqual(engine.get_frame_rate(), (25.0, False))
        cases = (
            ((0, 0, 0, 0), 0),
            ((0, 0, 0, 1), 1_920),
            ((0, 0, 0, 24), 46_080),
            ((0, 0, 1, 0), 48_000),
            ((0, 1, 0, 0), 2_880_000),
            ((10, 0, 0, 24), 1_728_046_080),
            ((10, 0, 1, 0), 1_728_048_000),
            ((10, 1, 0, 0), 1_730_880_000),
        )
        for position, expected in cases:
            with self.subTest(position=position):
                self.assertEqual(engine.timecode_to_samples(*position), expected)
                self.assertEqual(engine.duration_to_samples(*position), expected)
                self.assertEqual(
                    engine.samples_to_timecode(expected),
                    ":".join("%02d" % value for value in position),
                )

    def test_25fps_frame_round_trips_at_multiple_sample_rates(self):
        for rate, samples_per_frame in ((44_100, 1_764), (48_000, 1_920), (96_000, 3_840)):
            engine = TimecodeEngine(rate, 0x02)
            for frames in range(25 * 60 * 2):
                mm, remainder = divmod(frames, 25 * 60)
                ss, ff = divmod(remainder, 25)
                expected = frames * samples_per_frame
                self.assertEqual(engine.timecode_to_samples(0, mm, ss, ff), expected)
                self.assertEqual(
                    engine.samples_to_timecode(expected),
                    "00:%02d:%02d:%02d" % (mm, ss, ff),
                )

    def test_25fps_half_frame_rounding_and_second_minute_boundaries(self):
        engine = TimecodeEngine(48_000, 0x02)
        self.assertEqual(engine.samples_to_timecode(959), "00:00:00:00")
        self.assertEqual(engine.samples_to_timecode(960), "00:00:00:01")
        self.assertEqual(engine.samples_to_timecode(46_080), "00:00:00:24")
        self.assertEqual(engine.samples_to_timecode(48_000), "00:00:01:00")
        self.assertEqual(engine.samples_to_timecode(2_878_080), "00:00:59:24")
        self.assertEqual(engine.samples_to_timecode(2_880_000), "00:01:00:00")

    def test_25fps_rejects_invalid_frame_labels_and_component_types(self):
        engine = TimecodeEngine(48_000, 0x02)
        for convert in (engine.timecode_to_samples, engine.duration_to_samples):
            for frame in (-1, 25, 30):
                with self.subTest(conversion=convert.__name__, frame=frame):
                    with self.assertRaisesRegex(ValueError, "Invalid timecode"):
                        convert(0, 0, 0, frame)
            for frame in (True, 1.0):
                with self.assertRaisesRegex(TypeError, "integers"):
                    convert(0, 0, 0, frame)

    def test_25fps_timecode_support_does_not_expand_authoring_profiles(self):
        from tests.test_audio_session_builder import make_audio_import_template_session

        session = make_audio_import_template_session()
        session.frame_rate_enum = 0x02
        before = [root.to_bytes()[0] for root in session.root_items]
        with self.assertRaisesRegex(ValueError, "23.976"):
            session.validate_audio_import_template()
        self.assertEqual([root.to_bytes()[0] for root in session.root_items], before)
        with self.assertRaisesRegex(ValueError, "23.976"):
            session.create_empty_clip_group("Production 1", "GROUP", 0, 48_000)
        self.assertEqual([root.to_bytes()[0] for root in session.root_items], before)
        self.assertEqual(session._removed_offsets, [])

    def test_24_and_23976_positions_round_trip_exactly(self):
        positions = [
            (0, 0, 0, 0),
            (0, 0, 0, 1),
            (10, 0, 0, 0),
            (10, 59, 59, 23),
        ]
        for frame_rate_enum in (0x01, 0x09):
            engine = TimecodeEngine(48_000, frame_rate_enum)
            for position in positions:
                with self.subTest(rate=frame_rate_enum, position=position):
                    samples = engine.timecode_to_samples(*position)
                    expected = ":".join(
                        [
                            f"{position[0]:02d}",
                            f"{position[1]:02d}",
                            f"{position[2]:02d}",
                            f"{position[3]:02d}",
                        ]
                    )
                    self.assertEqual(engine.samples_to_timecode(samples), expected)

    def test_2997_drop_frame_boundaries_round_trip_exactly(self):
        engine = TimecodeEngine(48_000, 0x05)
        positions = [
            (0, 0, 59, 29),
            (0, 1, 0, 2),
            (0, 9, 59, 29),
            (0, 10, 0, 0),
            (10, 0, 0, 0),
        ]
        for position in positions:
            with self.subTest(position=position):
                samples = engine.timecode_to_samples(*position)
                expected = ":".join(f"{value:02d}" for value in position)
                self.assertEqual(engine.samples_to_timecode(samples), expected)

    def test_drop_frame_rejects_nonexistent_absolute_frame_numbers(self):
        engine = TimecodeEngine(48_000, 0x05)

        for frame in (0, 1):
            with self.subTest(frame=frame):
                with self.assertRaisesRegex(ValueError, "dropped frame"):
                    engine.timecode_to_samples(0, 1, 0, frame)

    def test_duration_allows_labels_that_are_invalid_as_df_positions(self):
        engine = TimecodeEngine(48_000, 0x05)

        duration = engine.duration_to_samples(0, 1, 0, 0)
        legal_position = engine.timecode_to_samples(0, 1, 0, 2)

        self.assertEqual(duration, legal_position)
        self.assertEqual(duration, 2_882_880)

    def test_invalid_components_and_samples_are_rejected(self):
        engine = TimecodeEngine(48_000, 0x09)

        with self.assertRaisesRegex(ValueError, "Invalid timecode"):
            engine.timecode_to_samples(0, 60, 0, 0)
        with self.assertRaisesRegex(ValueError, "Invalid timecode"):
            engine.timecode_to_samples(0, 0, 0, 24)
        with self.assertRaisesRegex(TypeError, "integers"):
            engine.timecode_to_samples(0, 0, 0, 1.0)
        with self.assertRaisesRegex(ValueError, "negative"):
            engine.samples_to_timecode(-1)
        with self.assertRaisesRegex(TypeError, "integer"):
            engine.samples_to_timecode(1.0)

    def test_invalid_engine_configuration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Sample rate"):
            TimecodeEngine(0, 0x01)
        with self.assertRaisesRegex(ValueError, "Sample rate"):
            TimecodeEngine(10**1000, 0x01)
        with self.assertRaisesRegex(TypeError, "enum"):
            TimecodeEngine(48_000, "24")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            TimecodeEngine(48_000, 0xFF).get_frame_rate()

    def test_unrepresentable_conversions_have_controlled_errors(self):
        engine = TimecodeEngine(48_000, 0x09)

        with self.assertRaisesRegex(ValueError, "conversion range"):
            engine.samples_to_timecode(10**1000)
        with self.assertRaisesRegex(ValueError, "conversion range"):
            engine.timecode_to_samples(10**1000, 0, 0, 0)
        with self.assertRaisesRegex(ValueError, "conversion range"):
            engine.duration_to_samples(10**1000, 0, 0, 0)


class Native25fpsTests(unittest.TestCase):
    @unittest.skipUnless(
        os.path.isfile(NATIVE_25FPS_RESAVED_FIXTURE),
        "Local Pro Tools-resaved 25-fps fixture not installed (not distributed).",
    )
    def test_one_frame_move_after_pro_tools_save_round_trip(self):
        with open(NATIVE_25FPS_RESAVED_FIXTURE, "rb") as stream:
            original = stream.read()
        session = ProToolsSession(NATIVE_25FPS_RESAVED_FIXTURE)
        self.assertEqual(session.sample_rate, 48_000)
        self.assertEqual(session.frame_rate_enum, 0x02)
        expected_markers = {
            "M25_FRAME24": "10:00:00:24",
            "M25_NEXT_SECOND": "10:00:01:00",
            "M25_MINUTE": "10:01:00:00",
        }
        with tempfile.TemporaryDirectory() as directory:
            for index in range(2):
                timeline = session.get_timeline_clips()
                self.assertEqual(len(timeline), 1)
                self.assertEqual(timeline[0]["start_samples"], 1_728_048_000)
                self.assertEqual(timeline[0]["end_samples"], 1_728_144_000)
                self.assertEqual(timeline[0]["length_samples"], 96_000)
                self.assertEqual(
                    {m["name"]: m["timecode"] for m in session.get_markers()},
                    expected_markers,
                )
                destination = os.path.join(directory, "noop_%d.ptx" % index)
                session.save(destination)
                with open(destination, "rb") as stream:
                    self.assertEqual(stream.read(), original)
                session = ProToolsSession(destination)
        with open(NATIVE_25FPS_RESAVED_FIXTURE, "rb") as stream:
            self.assertEqual(stream.read(), original)

    @unittest.skipUnless(
        os.path.isfile(NATIVE_25FPS_FIXTURE),
        "Local native 25-fps fixture not installed (not distributed).",
    )
    def test_native_markers_noop_and_one_frame_move(self):
        with open(NATIVE_25FPS_FIXTURE, "rb") as stream:
            original = stream.read()
        session = ProToolsSession(NATIVE_25FPS_FIXTURE)
        self.assertEqual(session.sample_rate, 48_000)
        self.assertEqual(session.frame_rate_enum, 0x02)
        self.assertEqual(len(session.get_tracks()), 1)
        expected_markers = {
            "M25_FRAME24": "10:00:00:24",
            "M25_NEXT_SECOND": "10:00:01:00",
            "M25_MINUTE": "10:01:00:00",
        }
        markers = session.get_markers()
        self.assertEqual({m["name"]: m["timecode"] for m in markers}, expected_markers)
        timeline = session.get_timeline_clips()
        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0]["start_samples"], 1_728_046_080)
        self.assertEqual(timeline[0]["length_samples"], 96_000)
        with tempfile.TemporaryDirectory() as directory:
            for index in range(2):
                destination = os.path.join(directory, "noop_%d.ptx" % index)
                session.save(destination)
                with open(destination, "rb") as stream:
                    self.assertEqual(stream.read(), original)
                session = ProToolsSession(destination)
                self.assertEqual(session.get_timeline_clips(), timeline)
                self.assertEqual(session.get_markers(), markers)

            expected_data = bytearray(session.data)
            event = session._validated_main_timeline_events()[0]
            payload_offset = event[2].original_offset + 9
            struct.pack_into("<Q", expected_data, payload_offset + 7, 1_728_048_000)
            self.assertEqual(session.move_clip(timeline[0]["clip_name"], 10, 0, 1, 0), 1)
            moved_path = os.path.join(directory, "moved.ptx")
            session.save(moved_path)
            self.assertEqual(session.data, expected_data)
            reloaded = ProToolsSession(moved_path)
            moved = reloaded.get_timeline_clips()
            self.assertEqual(len(moved), 1)
            self.assertEqual(moved[0]["start_samples"], 1_728_048_000)
            self.assertEqual(moved[0]["end_samples"], 1_728_144_000)
            self.assertEqual(moved[0]["length_samples"], 96_000)
            self.assertEqual(reloaded.get_markers(), markers)
        with open(NATIVE_25FPS_FIXTURE, "rb") as stream:
            self.assertEqual(stream.read(), original)


if __name__ == "__main__":
    unittest.main()
