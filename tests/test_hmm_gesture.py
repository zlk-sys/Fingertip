import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.hmm_gesture import (
    FeatureExtractor,
    HMMRecognizer,
    MotionSegmenter,
    SignalFilter,
    load_gesture_data,
    save_gesture,
    train_directory,
)


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


if __name__ == '__main__':
    unittest.main()
