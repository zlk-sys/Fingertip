import unittest

import numpy as np

from app.trajectory import TrackingPhase, TrajectoryEngine
from app.trajectory.orientation import OrientationFilter, rotation_matrix


class _RingReplay:
    DT = 0.02

    def __init__(self, engine, initial_rotation=None):
        self.engine = engine
        self.rotation = (
            np.eye(3) if initial_rotation is None
            else np.asarray(initial_rotation, dtype=np.float64).copy())
        self.gravity_world = np.array([0.0, 0.0, 1.0])
        self.bias_body_dps = np.array([1.2, -0.7, 0.5])
        self.timestamp_ms = 0
        self.last_frame = None

    def feed(self, world_rate_dps, steps, gyro_noise_raw=0):
        world_rate = np.asarray(world_rate_dps, dtype=np.float64)
        for index in range(steps):
            body_rate = self.rotation.T @ world_rate
            self.rotation = (
                self.rotation
                @ rotation_matrix(np.deg2rad(body_rate) * self.DT))
            accel_body = self.rotation.T @ self.gravity_world
            total_rate = body_rate + self.bias_body_dps
            noise = ((index % 5) - 2) * gyro_noise_raw
            self.timestamp_ms = (self.timestamp_ms + 20) & 0xFFFFFFFF
            self.last_frame = self.engine.process_raw(
                self.timestamp_ms,
                *(np.rint(accel_body * 16384).astype(int)),
                *(np.rint(total_rate * 32768 / 2000).astype(int) + noise),
            )
        return self.last_frame

    def stabilize(self):
        self.feed([0.0, 0.0, 0.0], 45)
        assert self.engine.bias_ready

    def calibrate(self):
        self.assert_phase(TrackingPhase.READY)
        assert self.engine.begin_wearing_calibration()
        self.feed([0.0, 0.0, -40.0], 25)
        self.feed([0.0, 0.0, 0.0], 18)
        self.assert_phase(TrackingPhase.CALIBRATING_UP)
        self.feed([0.0, -40.0, 0.0], 25)
        self.feed([0.0, 0.0, 0.0], 8)
        self.assert_phase(TrackingPhase.TRACKING)
        assert self.engine.calibrated

    def assert_phase(self, expected):
        if self.engine.phase != expected:
            raise AssertionError(
                f'expected phase {expected}, got {self.engine.phase}')


class TrajectoryEngineTest(unittest.TestCase):

    def make_replay(self, initial_rotation=None):
        engine = TrajectoryEngine()
        engine.configure(2, 2000, 50)
        return _RingReplay(engine, initial_rotation)

    def test_stationary_position_is_exactly_frozen(self):
        replay = self.make_replay()
        replay.stabilize()
        replay.feed([0.0, 0.0, -50.0], 30)
        replay.feed([0.0, 0.0, 0.0], 20)
        stopped = replay.engine.position
        replay.feed([0.0, 0.0, 0.0], 500, gyro_noise_raw=4)
        np.testing.assert_array_equal(replay.engine.position, stopped)

    def test_bootstrap_accepts_stable_real_device_gyro_bias(self):
        replay = self.make_replay()
        replay.bias_body_dps = np.array([9.32, -4.57, 0.61])

        replay.stabilize()

        self.assertTrue(replay.engine.bias_ready)
        self.assertEqual(replay.engine.phase, TrackingPhase.READY)
        np.testing.assert_allclose(
            replay.engine._gyro_bias, replay.bias_body_dps, atol=0.07)

    def test_same_angle_has_same_length_at_different_speeds(self):
        positions = []
        for rate in (15.0, 30.0, 60.0, 100.0):
            replay = self.make_replay()
            replay.stabilize()
            replay.feed([0.0, 0.0, -rate], round((30.0 / rate) / 0.02))
            replay.feed([0.0, 0.0, 0.0], 15)
            positions.append(float(replay.engine.position[0]))

        self.assertLess(max(positions) - min(positions), 0.08)
        for position in positions:
            self.assertAlmostEqual(position, 30.0, delta=0.08)

    def test_180_degree_wearing_rotation_produces_same_trajectory(self):
        normal = self.make_replay()
        rolled = self.make_replay(
            rotation_matrix(np.array([0.0, np.pi, 0.0])))

        results = []
        for replay in (normal, rolled):
            replay.stabilize()
            replay.calibrate()
            replay.feed([35.0, 20.0, 10.0], 20)
            replay.feed([0.0, 0.0, 0.0], 15)
            start = replay.engine.position
            replay.feed([0.0, -15.0, -25.0], 50)
            replay.feed([0.0, 0.0, 0.0], 15)
            results.append(replay.engine.position - start)

        np.testing.assert_allclose(results[0], results[1], atol=0.25)
        self.assertGreater(abs(results[0][0]), 20.0)
        self.assertGreater(abs(results[0][1]), 10.0)

    def test_cancelled_recalibration_keeps_previous_mapping(self):
        replay = self.make_replay()
        replay.stabilize()
        replay.calibrate()
        right_before = replay.engine._right_axis.copy()
        up_before = replay.engine._up_axis.copy()

        self.assertTrue(replay.engine.begin_wearing_calibration())
        replay.feed([30.0, 10.0, -40.0], 10)
        self.assertTrue(replay.engine.cancel_wearing_calibration())

        np.testing.assert_array_equal(replay.engine._right_axis, right_before)
        np.testing.assert_array_equal(replay.engine._up_axis, up_before)
        self.assertEqual(replay.engine.phase, TrackingPhase.TRACKING)


class OrientationFilterTest(unittest.TestCase):

    def test_accelerometer_feedback_bounds_stationary_tilt(self):
        orientation = OrientationFilter()
        orientation.reset([0.0, 0.0, 1.0])
        for _ in range(2000):
            orientation.update(
                [0.5, 0.0, 0.0],
                [0.0, 0.0, 1.0],
                0.01,
                stationary=True,
            )
        gravity_ref = orientation.body_to_reference([0.0, 0.0, 1.0])
        tilt = np.degrees(np.arccos(np.clip(gravity_ref[2], -1.0, 1.0)))
        self.assertLess(tilt, 0.25)


if __name__ == '__main__':
    unittest.main()
