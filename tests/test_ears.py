from __future__ import annotations

import importlib.util
import math
import pathlib
import tempfile
import unittest

from daemon_vessel.ears import SUB_BANDS, analyze_audio, frequency_to_note, observation


AUDIO_DEPS_AVAILABLE = all(
    importlib.util.find_spec(name) is not None
    for name in ("librosa", "numpy", "scipy", "soundfile")
)


class EvidenceContractTests(unittest.TestCase):
    def test_frequency_to_note_respects_435_reference(self):
        note = frequency_to_note(435.0, a4_hz=435.0)
        self.assertIsNotNone(note)
        self.assertEqual(note["note"], "A4")
        self.assertAlmostEqual(note["cents"], 0.0, places=3)

    def test_frequency_to_note_tracks_octave(self):
        note = frequency_to_note(217.5, a4_hz=435.0)
        self.assertEqual(note["note"], "A3")

    def test_observation_rejects_unknown_evidence_status(self):
        with self.assertRaises(ValueError):
            observation(
                "counterfeit",
                "feeling",
                unit=None,
                method="wishful thinking",
                confidence=1.0,
                status="pretended",
            )

    def test_sub_bands_are_contiguous_and_non_overlapping(self):
        self.assertEqual(SUB_BANDS[0][1], 20.0)
        self.assertEqual(SUB_BANDS[-1][2], 250.0)
        for left, right in zip(SUB_BANDS, SUB_BANDS[1:]):
            self.assertEqual(left[2], right[1])
            self.assertLess(left[1], left[2])


@unittest.skipUnless(AUDIO_DEPS_AVAILABLE, "optional ears dependencies are not installed")
class AudioAnalysisTests(unittest.TestCase):
    def test_synthetic_stereo_body_produces_provenance(self):
        import numpy as np
        import soundfile as sf

        sr = 22050
        duration_s = 8.0
        t = np.arange(int(sr * duration_s)) / sr
        pulse_hz = 174.0 / 60.0
        gate = (np.sin(2 * np.pi * pulse_hz * t) > 0.75).astype(float)
        left = 0.25 * np.sin(2 * np.pi * 60.0 * t) * (0.25 + 0.75 * gate)
        right = left.copy()
        stereo = np.column_stack([left, right])

        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "synthetic-pressure.wav"
            sf.write(path, stereo, sr)
            result = analyze_audio(path, a4_hz=435.0)

        self.assertEqual(result["schema_version"], "glitch-ears/0.1")
        self.assertEqual(result["source"]["channels"], 2)
        self.assertEqual(len(result["source"]["sha256"]), 64)
        self.assertIn("emotion", result["not_measured"])

        by_name = {item["name"]: item for item in result["observations"]}
        self.assertIn("band_energy_primary_sub", by_name)
        self.assertIn("low_band_mono_compatibility", by_name)
        self.assertEqual(by_name["low_band_mono_compatibility"]["status"], "measured")
        self.assertGreater(by_name["low_band_mono_compatibility"]["value"], 0.99)

        for item in result["observations"]:
            self.assertIn(item["status"], {"measured", "inferred", "relational"})
            self.assertTrue(math.isfinite(item["confidence"]))
            self.assertGreaterEqual(item["confidence"], 0.0)
            self.assertLessEqual(item["confidence"], 1.0)


if __name__ == "__main__":
    unittest.main()
