import unittest

from pt_api import TimecodeEngine


class TimecodeEngineTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
