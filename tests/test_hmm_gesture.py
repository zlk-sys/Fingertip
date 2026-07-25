import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.hmm_gesture import (
    FeatureExtractor,
    HMMRecognizer,
    MotionSegmenter,
    RobustPreprocessor,
    SignalFilter,
    load_gesture_data,
    save_gesture,
    train_directory,
)
from app.hmm_gesture.core import _apply_fixed_rotation


RESOURCE_DIR = Path(__file__).resolve().parents[1] / 'app' / 'hmm_gesture'
WORKSPACE_DIR = Path(__file__).resolve().parents[1]


class HMMGestureCoreTest(unittest.TestCase):

    def test_filter_and_feature_dimensions(self):
        raw = np.arange(30 * 6, dtype=np.int16).reshape(30, 6)
        filtered = SignalFilter().apply(raw)
        features = FeatureExtractor().extract(filtered)

        self.assertEqual(filtered.shape, raw.shape)
        self.assertGreater(len(features), 1)
        self.assertEqual(features.shape[1], 24)
        self.assertTrue(np.isfinite(features).all())

    def test_motion_segmenter_returns_complete_motion(self):
        segmenter = MotionSegmenter(
            energy_threshold=100.0,
            min_onset_frames=2,
            min_offset_frames=2,
            min_gesture_len=5,
            pre_roll=2,
            cooldown_frames=0,
        )
        still = [[0, 0, 0, 0, 0, 0]] * 6
        moving = [[0, 0, 0, 300, 0, 0]] * 10

        segments = segmenter.feed(still + moving + still)

        self.assertEqual(len(segments), 1)
        self.assertGreaterEqual(len(segments[0]), 10)
        self.assertEqual(segments[0].shape[1], 6)

    def test_gesture_json_round_trip(self):
        repetition = np.arange(20 * 6, dtype=np.int16).reshape(20, 6)
        with tempfile.TemporaryDirectory(
                dir=WORKSPACE_DIR, prefix='.hmm-test-') as directory:
            path = save_gesture(
                '测试/动作',
                [repetition, repetition + 1],
                directory,
                sample_rate_hz=25,
            )
            name, rate, repetitions = load_gesture_data(path)

        self.assertEqual(path.name, '测试_动作.json')
        self.assertEqual(name, '测试/动作')
        self.assertEqual(rate, 25)
        np.testing.assert_array_equal(repetitions[0], repetition)
        np.testing.assert_array_equal(repetitions[1], repetition + 1)

    def test_pretrained_model_recognizes_reference_sample(self):
        recognizer = HMMRecognizer(RESOURCE_DIR / 'pretrained_models')
        _, _, repetitions = load_gesture_data(
            RESOURCE_DIR / 'sample_data' / '向上.json')

        result = recognizer.classify_segment(repetitions[0])

        self.assertIsNotNone(result)
        self.assertEqual(result[0], '向上')
        self.assertGreater(result[1], 0.85)

        decision = recognizer.analyze_segment(repetitions[0])
        payload = decision.to_payload()
        self.assertEqual(decision.status, 'confirmed')
        self.assertEqual(decision.best_candidate.name, '向上')
        self.assertEqual(payload['candidates'][0]['token'], '向上')

    def test_pretrained_models_reject_unrelated_motion(self):
        recognizer = HMMRecognizer(
            RESOURCE_DIR / 'pretrained_models',
            min_confidence=0.35,
        )
        unrelated = np.zeros((200, 6), dtype=np.int16)
        unrelated[:, 0] = np.tile(
            np.array([0, 30000, -30000, 0], dtype=np.int16), 50)
        unrelated[:, 3] = np.tile(
            np.array([25000, -25000, 0, 0], dtype=np.int16), 50)

        result = recognizer.classify_segment(unrelated)
        decision = recognizer.analyze_segment(unrelated)

        self.assertIsNone(result)
        self.assertNotEqual(decision.status, 'confirmed')
        self.assertTrue(decision.candidates)
        self.assertEqual(
            set(recognizer.acceptance_thresholds),
            set(recognizer.model_names),
        )

    def test_train_directory_creates_loadable_model(self):
        with tempfile.TemporaryDirectory(
                dir=WORKSPACE_DIR, prefix='.hmm-test-') as directory:
            root = Path(directory)
            data_dir = root / 'data'
            model_dir = root / 'models'
            data_dir.mkdir()
            shutil.copy2(
                RESOURCE_DIR / 'sample_data' / '打响指-hmm.json',
                data_dir / '打响指-hmm.json',
            )

            result = train_directory(data_dir, model_dir)
            recognizer = HMMRecognizer(model_dir)

        self.assertEqual(result.trained, ('打响指-hmm',))
        self.assertFalse(result.failed)
        self.assertEqual(recognizer.model_names, ('打响指-hmm',))
        trained_model = recognizer._models['打响指-hmm']
        self.assertTrue(hasattr(
            trained_model, 'gesture_acceptance_threshold_'))
        self.assertTrue(np.isfinite(
            trained_model.gesture_acceptance_threshold_))


class RobustnessTest(unittest.TestCase):
    """Verify orientation and amplitude robustness of the v3 pipeline."""

    @classmethod
    def setUpClass(cls):
        """Train a model with the new pipeline for robustness tests."""
        cls._tmpdir = tempfile.TemporaryDirectory(
            dir=WORKSPACE_DIR, prefix='.hmm-robust-')
        root = Path(cls._tmpdir.name)
        cls.data_dir = root / 'data'
        cls.model_dir = root / 'models'
        cls.data_dir.mkdir()
        shutil.copy2(
            RESOURCE_DIR / 'sample_data' / '打响指-hmm.json',
            cls.data_dir / '打响指-hmm.json',
        )
        train_directory(cls.data_dir, cls.model_dir)
        cls.recognizer = HMMRecognizer(cls.model_dir)
        _, _, cls.repetitions = load_gesture_data(
            cls.data_dir / '打响指-hmm.json')

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()

    def test_new_model_has_pipeline_version_3(self):
        model = self.recognizer._models['打响指-hmm']
        self.assertEqual(model.gesture_pipeline_version_, 3)

    def test_recognizes_original_gesture(self):
        result = self.recognizer.classify_segment(self.repetitions[0])
        self.assertIsNotNone(result)
        self.assertEqual(result[0], '打响指-hmm')

    def test_recognizes_rotated_45_degrees(self):
        rotated = _apply_fixed_rotation(
            self.repetitions[0].astype(np.float64), 45.0)
        result = self.recognizer.classify_segment(rotated)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], '打响指-hmm')

    def test_recognizes_rotated_90_degrees(self):
        rotated = _apply_fixed_rotation(
            self.repetitions[0].astype(np.float64), 90.0)
        result = self.recognizer.classify_segment(rotated)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], '打响指-hmm')

    def test_recognizes_rotated_135_degrees(self):
        rotated = _apply_fixed_rotation(
            self.repetitions[0].astype(np.float64), 135.0)
        result = self.recognizer.classify_segment(rotated)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], '打响指-hmm')

    def test_recognizes_rotated_180_degrees(self):
        rotated = _apply_fixed_rotation(
            self.repetitions[0].astype(np.float64), 180.0)
        result = self.recognizer.classify_segment(rotated)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], '打响指-hmm')

    def test_recognizes_negative_rotation(self):
        rotated = _apply_fixed_rotation(
            self.repetitions[0].astype(np.float64), -90.0)
        result = self.recognizer.classify_segment(rotated)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], '打响指-hmm')

    def test_recognizes_amplitude_scaled_down_0_7(self):
        scaled = self.repetitions[0].astype(np.float64) * 0.7
        result = self.recognizer.classify_segment(scaled)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], '打响指-hmm')

    def test_recognizes_amplitude_scaled_up_1_3(self):
        scaled = self.repetitions[0].astype(np.float64) * 1.3
        result = self.recognizer.classify_segment(scaled)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], '打响指-hmm')

    def test_recognizes_combined_rotation_and_scaling(self):
        transformed = _apply_fixed_rotation(
            self.repetitions[0].astype(np.float64) * 0.8, 60.0)
        result = self.recognizer.classify_segment(transformed)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], '打响指-hmm')

    def test_preprocessor_output_shape(self):
        raw = np.random.randn(30, 6) * 1000
        filtered = SignalFilter().apply(raw)
        preprocessed = RobustPreprocessor().apply(filtered)
        self.assertEqual(preprocessed.shape, (30, 6))
        self.assertTrue(np.isfinite(preprocessed).all())

    def test_preprocessor_amplitude_normalization(self):
        """Different amplitude inputs should produce similar output RMS."""
        np.random.seed(42)
        base = np.random.randn(50, 6) * 1000
        base[:, 2] += 9800  # add gravity-like offset
        scaled_07 = base * 0.7
        scaled_13 = base * 1.3

        prep = RobustPreprocessor()
        out_base = prep.apply(base)
        out_07 = prep.apply(scaled_07)
        out_13 = prep.apply(scaled_13)

        rms_base = np.sqrt(np.mean(out_base ** 2))
        rms_07 = np.sqrt(np.mean(out_07 ** 2))
        rms_13 = np.sqrt(np.mean(out_13 ** 2))

        # All outputs should have similar RMS after normalization
        self.assertAlmostEqual(rms_base, rms_07, delta=rms_base * 0.15)
        self.assertAlmostEqual(rms_base, rms_13, delta=rms_base * 0.15)


if __name__ == '__main__':
    unittest.main()
