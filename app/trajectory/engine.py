# coding: utf-8
"""Pure IMU-to-2D trajectory engine.

This module owns sensor units, gyro bias, stationary detection, orientation,
wearing calibration and trajectory integration.  It has no Qt dependency and
can be replay-tested with deterministic IMU samples.
"""

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np

from .orientation import OrientationFilter


class TrackingPhase(str, Enum):
    STABILIZING = 'stabilizing'
    READY = 'ready'
    CALIBRATING_RIGHT = 'calibrating_right'
    CALIBRATING_STILL = 'calibrating_still'
    CALIBRATING_UP = 'calibrating_up'
    TRACKING = 'tracking'


@dataclass(frozen=True)
class TrajectoryFrame:
    x: float
    y: float
    dx: float
    dy: float
    moved: bool
    stationary: bool
    calibrated: bool
    phase: TrackingPhase
    event: Optional[str] = None


class _MotionAxisCapture:
    """Capture one intentional swing and estimate its dominant signed axis."""

    START_DPS = 8.0
    END_DPS = 3.0
    MIN_SAMPLES = 4
    END_SAMPLES = 5
    MAX_SAMPLES = 120

    def __init__(self):
        self.samples = []
        self.started = False
        self.end_count = 0

    def feed(self, angular_velocity):
        vector = np.asarray(angular_velocity, dtype=np.float64)
        magnitude = float(np.linalg.norm(vector))

        if magnitude >= self.START_DPS:
            self.started = True
            self.end_count = 0
            self.samples.append(vector.copy())
            if len(self.samples) >= self.MAX_SAMPLES:
                return self.finish()
            return None

        if self.started and magnitude <= self.END_DPS:
            self.end_count += 1
            if (self.end_count >= self.END_SAMPLES
                    and len(self.samples) >= self.MIN_SAMPLES):
                return self.finish()
        return None

    def finish(self):
        if len(self.samples) < self.MIN_SAMPLES:
            return None
        samples = np.asarray(self.samples)
        magnitudes = np.linalg.norm(samples, axis=1)
        peak = samples[int(np.argmax(magnitudes))]

        # PCA finds the motion axis even if the user partly swings back.
        covariance = samples.T @ samples
        values, vectors = np.linalg.eigh(covariance)
        axis = vectors[:, int(np.argmax(values))]
        if float(np.dot(axis, peak)) < 0.0:
            axis = -axis

        aligned = samples[samples @ axis > 0.0]
        if len(aligned) < self.MIN_SAMPLES:
            return None
        signed_mean = aligned.sum(axis=0)
        norm = float(np.linalg.norm(signed_mean))
        if norm < 1e-6:
            return None
        return signed_mean / norm


class TrajectoryEngine:
    """Convert timestamped raw six-axis samples into a stable 2D path."""

    ACCEL_ENTER_TOL_G = 0.10
    ACCEL_EXIT_TOL_G = 0.20
    GYRO_ENTER_DPS = 1.0
    GYRO_EXIT_DPS = 1.8
    STILL_SAMPLES = 7
    BIAS_SAMPLES = 30
    # The ring can have a sizeable but stable factory gyro offset.  Recorded
    # stationary hardware data reaches about 10.4 dps, so an 8 dps absolute
    # gate rejects every sample and leaves startup stuck in STABILIZING.
    # Window variance below still distinguishes a stable zero bias from
    # ordinary hand movement; this ceiling only rejects clearly moving data.
    BIAS_MAX_RATE_DPS = 20.0
    BIAS_MAX_STD_DPS = 0.8
    BIAS_MAX_ACCEL_STD_G = 0.025
    BIAS_TRACK_RATE = 0.025
    ACCEL_FILTER_ALPHA = 0.30
    RATE_NOISE_GATE_DPS = 0.20
    MAX_DT_S = 0.10
    CALIBRATION_TIMEOUT_S = 8.0
    CALIBRATION_STILL_SAMPLES = 8

    def __init__(self):
        self.orientation = OrientationFilter()
        self.accel_range_g = 2.0
        self.gyro_range_dps = 2000.0
        self.sample_rate_hz = 50.0
        self.sensitivity = 5
        self.reset()

    @property
    def bias_ready(self):
        return self._bias_ready

    @property
    def calibrated(self):
        return self._calibrated

    @property
    def stationary(self):
        return self._stationary

    @property
    def position(self):
        return self._position.copy()

    def configure(
            self,
            accel_range_g,
            gyro_range_dps,
            sample_rate_hz=None):
        self.accel_range_g = float(accel_range_g or 2.0)
        self.gyro_range_dps = float(gyro_range_dps or 2000.0)
        if sample_rate_hz:
            self.sample_rate_hz = float(sample_rate_hz)

    def set_sensitivity(self, value):
        self.sensitivity = max(1, min(10, int(value)))

    def reset(self):
        self.orientation.reset()
        self.phase = TrackingPhase.STABILIZING
        self._position = np.zeros(2, dtype=np.float64)
        self._last_timestamp_ms = None
        self._accel_filtered = None
        self._gyro_bias = np.zeros(3, dtype=np.float64)
        self._bias_ready = False
        self._bias_gyro = deque(maxlen=self.BIAS_SAMPLES)
        self._bias_accel = deque(maxlen=self.BIAS_SAMPLES)
        self._stationary = False
        self._still_count = 0
        self._calibrated = False
        self._right_axis = None
        self._up_axis = None
        self._calibration_right_axis = None
        self._right_capture = None
        self._up_capture = None
        self._calibration_still_count = 0
        self._phase_elapsed_s = 0.0
        self._previous_rate = np.zeros(2, dtype=np.float64)
        self._rate_valid = False
        self._pending_event = None

    def recenter(self):
        self._position[:] = 0.0
        self._reset_integrator()

    def begin_wearing_calibration(self):
        if not self._bias_ready:
            return False
        self.phase = TrackingPhase.CALIBRATING_RIGHT
        self._right_capture = _MotionAxisCapture()
        self._up_capture = None
        self._calibration_right_axis = None
        self._phase_elapsed_s = 0.0
        self._calibration_still_count = 0
        self._reset_integrator()
        self._pending_event = 'calibration_started'
        return True

    def cancel_wearing_calibration(self):
        if self.phase not in {
                TrackingPhase.CALIBRATING_RIGHT,
                TrackingPhase.CALIBRATING_STILL,
                TrackingPhase.CALIBRATING_UP}:
            return False
        self.phase = (
            TrackingPhase.TRACKING if self._calibrated
            else TrackingPhase.READY)
        self._right_capture = None
        self._up_capture = None
        self._calibration_right_axis = None
        self._phase_elapsed_s = 0.0
        self._pending_event = 'calibration_cancelled'
        return True

    def _frame(self, delta=None, event=None):
        if delta is None:
            delta = np.zeros(2, dtype=np.float64)
        if event is None:
            event = self._pending_event
        self._pending_event = None
        return TrajectoryFrame(
            x=float(self._position[0]),
            y=float(self._position[1]),
            dx=float(delta[0]),
            dy=float(delta[1]),
            moved=bool(np.any(np.abs(delta) > 1e-9)),
            stationary=self._stationary,
            calibrated=self._calibrated,
            phase=self.phase,
            event=event,
        )

    def _reset_integrator(self):
        self._previous_rate[:] = 0.0
        self._rate_valid = False

    def _timestamp_delta(self, timestamp_ms):
        timestamp = int(timestamp_ms) & 0xFFFFFFFF
        if self._last_timestamp_ms is None:
            self._last_timestamp_ms = timestamp
            return None
        elapsed_ms = (timestamp - self._last_timestamp_ms) & 0xFFFFFFFF
        self._last_timestamp_ms = timestamp
        if elapsed_ms == 0 or elapsed_ms >= 0x80000000:
            return None
        return elapsed_ms / 1000.0

    def _try_bootstrap(self, accel_g, gyro_dps):
        accel_error = abs(float(np.linalg.norm(accel_g)) - 1.0)
        if (accel_error <= self.ACCEL_ENTER_TOL_G
                and float(np.linalg.norm(gyro_dps))
                <= self.BIAS_MAX_RATE_DPS):
            self._bias_gyro.append(gyro_dps.copy())
            self._bias_accel.append(accel_g.copy())
        else:
            self._bias_gyro.clear()
            self._bias_accel.clear()
            return False

        if len(self._bias_gyro) < self.BIAS_SAMPLES:
            return False
        gyro_window = np.asarray(self._bias_gyro)
        accel_window = np.asarray(self._bias_accel)
        if (float(gyro_window.std(axis=0).max())
                > self.BIAS_MAX_STD_DPS
                or float(accel_window.std(axis=0).max())
                > self.BIAS_MAX_ACCEL_STD_G):
            return False

        self._gyro_bias = gyro_window.mean(axis=0)
        self._bias_ready = True
        self._stationary = True
        self._still_count = self.STILL_SAMPLES
        gravity = accel_window.mean(axis=0)
        self.orientation.reset(gravity)
        self._set_default_plane(gravity)
        self.phase = TrackingPhase.READY
        self._pending_event = 'bias_ready'
        self._reset_integrator()
        return True

    def _set_default_plane(self, accel_g):
        gravity_body = np.asarray(accel_g, dtype=np.float64)
        gravity_body /= np.linalg.norm(gravity_body)
        gravity_ref = self.orientation.body_to_reference(gravity_body)
        gravity_ref /= np.linalg.norm(gravity_ref)

        horizontal_body = np.array([0.0, 1.0, 0.0])
        horizontal_body -= (
            np.dot(horizontal_body, gravity_body) * gravity_body)
        if float(np.linalg.norm(horizontal_body)) < 0.2:
            horizontal_body = np.array([1.0, 0.0, 0.0])
            horizontal_body -= (
                np.dot(horizontal_body, gravity_body) * gravity_body)
        horizontal_body /= np.linalg.norm(horizontal_body)
        horizontal_ref = self.orientation.body_to_reference(horizontal_body)
        horizontal_ref -= (
            np.dot(horizontal_ref, gravity_ref) * gravity_ref)
        horizontal_ref /= np.linalg.norm(horizontal_ref)

        # Match the intuitive air-mouse defaults until guided calibration.
        self._right_axis = -gravity_ref
        self._up_axis = -horizontal_ref

    def _update_stationary(self, accel_g, gyro_dps):
        accel_error = abs(float(np.linalg.norm(accel_g)) - 1.0)
        residual = gyro_dps - self._gyro_bias
        gyro_error = float(np.linalg.norm(residual))

        if self._stationary:
            if (accel_error >= self.ACCEL_EXIT_TOL_G
                    or gyro_error >= self.GYRO_EXIT_DPS):
                self._stationary = False
                self._still_count = 0
            else:
                beta = self.BIAS_TRACK_RATE
                self._gyro_bias = (
                    (1.0 - beta) * self._gyro_bias + beta * gyro_dps)
                self._reset_integrator()
            return

        if (accel_error <= self.ACCEL_ENTER_TOL_G
                and gyro_error <= self.GYRO_ENTER_DPS):
            self._still_count += 1
            self._reset_integrator()
            if self._still_count >= self.STILL_SAMPLES:
                self._stationary = True
        else:
            self._still_count = 0

    def _fail_calibration(self, reason):
        self.phase = (
            TrackingPhase.TRACKING if self._calibrated
            else TrackingPhase.READY)
        self._right_capture = None
        self._up_capture = None
        self._calibration_right_axis = None
        self._phase_elapsed_s = 0.0
        self._pending_event = reason
        self._reset_integrator()

    def _process_calibration(self, angular_velocity_ref, dt):
        self._phase_elapsed_s += dt
        if self._phase_elapsed_s > self.CALIBRATION_TIMEOUT_S:
            self._fail_calibration('calibration_timeout')
            return

        if self.phase == TrackingPhase.CALIBRATING_RIGHT:
            axis = self._right_capture.feed(angular_velocity_ref)
            if axis is not None:
                self._calibration_right_axis = axis
                self.phase = TrackingPhase.CALIBRATING_STILL
                self._phase_elapsed_s = 0.0
                self._calibration_still_count = 0
                self._pending_event = 'calibration_right_done'
            return

        if self.phase == TrackingPhase.CALIBRATING_STILL:
            if self._stationary:
                self._calibration_still_count += 1
            else:
                self._calibration_still_count = 0
            if (self._calibration_still_count
                    >= self.CALIBRATION_STILL_SAMPLES):
                self.phase = TrackingPhase.CALIBRATING_UP
                self._up_capture = _MotionAxisCapture()
                self._phase_elapsed_s = 0.0
                self._pending_event = 'calibration_up_ready'
            return

        if self.phase == TrackingPhase.CALIBRATING_UP:
            axis = self._up_capture.feed(angular_velocity_ref)
            if axis is None:
                return
            if abs(float(np.dot(
                    self._calibration_right_axis, axis))) > 0.88:
                self._fail_calibration('calibration_axes_too_close')
                return

            up_axis = axis - (
                np.dot(axis, self._calibration_right_axis)
                * self._calibration_right_axis)
            up_axis /= np.linalg.norm(up_axis)
            self._right_axis = self._calibration_right_axis
            self._up_axis = up_axis
            self._calibration_right_axis = None
            self._calibrated = True
            self.phase = TrackingPhase.TRACKING
            self.recenter()
            self._pending_event = 'calibration_complete'

    def process_raw(
            self,
            timestamp_ms,
            accel_x,
            accel_y,
            accel_z,
            gyro_x,
            gyro_y,
            gyro_z):
        accel_scale = self.accel_range_g / 32768.0
        gyro_scale = self.gyro_range_dps / 32768.0
        accel_g = np.array(
            [accel_x, accel_y, accel_z], dtype=np.float64) * accel_scale
        gyro_dps = np.array(
            [gyro_x, gyro_y, gyro_z], dtype=np.float64) * gyro_scale

        if self._accel_filtered is None:
            self._accel_filtered = accel_g.copy()
        else:
            alpha = self.ACCEL_FILTER_ALPHA
            self._accel_filtered = (
                alpha * accel_g + (1.0 - alpha) * self._accel_filtered)

        dt = self._timestamp_delta(timestamp_ms)
        if not self._bias_ready:
            self._try_bootstrap(self._accel_filtered, gyro_dps)
            return self._frame()
        if dt is None or dt > self.MAX_DT_S:
            self._reset_integrator()
            return self._frame()

        self._update_stationary(self._accel_filtered, gyro_dps)
        body_rate = gyro_dps - self._gyro_bias
        zero_candidate = self._stationary or self._still_count > 0
        attitude_accel = self._accel_filtered if zero_candidate else accel_g
        self.orientation.update(
            body_rate, attitude_accel, dt, stationary=zero_candidate)
        reference_rate = self.orientation.body_to_reference(body_rate)

        if self.phase in {
                TrackingPhase.CALIBRATING_RIGHT,
                TrackingPhase.CALIBRATING_STILL,
                TrackingPhase.CALIBRATING_UP}:
            self._process_calibration(reference_rate, dt)
            self._reset_integrator()
            return self._frame()

        if zero_candidate:
            self._reset_integrator()
            return self._frame()

        canvas_rate = np.array([
            np.dot(reference_rate, self._right_axis),
            np.dot(reference_rate, self._up_axis),
        ])
        canvas_rate[np.abs(canvas_rate) <= self.RATE_NOISE_GATE_DPS] = 0.0

        if self._rate_valid:
            integrated_rate = 0.5 * (self._previous_rate + canvas_rate)
        else:
            integrated_rate = canvas_rate
            self._rate_valid = True
        self._previous_rate = canvas_rate

        pixels_per_degree = self.sensitivity / 5.0
        delta = integrated_rate * dt * pixels_per_degree
        self._position += delta
        return self._frame(delta)
