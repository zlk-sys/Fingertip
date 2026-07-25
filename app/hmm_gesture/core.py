# coding: utf-8
"""HMM gesture pipeline adapted from the ring SDK reference implementation.

The SDK ships the collection, training and recognition stages as three CLI
programs.  This module keeps the same algorithms while exposing reusable,
UI-safe functions that operate on the application's shared BLE stream.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np
from hmmlearn import hmm
from scipy.signal import butter, medfilt, sosfilt


class SignalFilter:
    """Median filter followed by a second-order Butterworth low-pass."""

    def __init__(
            self,
            sample_rate: float = 25.0,
            cutoff_hz: float = 10.0,
            order: int = 2,
            median_kernel: int = 5):
        self.sample_rate = max(1.0, float(sample_rate))
        nyquist = self.sample_rate / 2.0
        self.cutoff_hz = min(max(0.1, float(cutoff_hz)), nyquist * 0.95)
        self.order = int(order)
        kernel = max(1, int(median_kernel))
        self.median_kernel = kernel if kernel % 2 else kernel + 1
        self._sos = butter(
            self.order,
            self.cutoff_hz / nyquist,
            btype='low',
            output='sos',
        )

    def apply(self, raw: np.ndarray) -> np.ndarray:
        """Filter an ``(N, 6)`` raw IMU array."""
        data = _as_imu_array(raw).astype(np.float64, copy=True)
        if len(data) == 0:
            return data
        for axis in range(data.shape[1]):
            if self.median_kernel > 1:
                kernel = min(self.median_kernel, len(data))
                if kernel % 2 == 0:
                    kernel -= 1
                if kernel > 1:
                    data[:, axis] = medfilt(
                        data[:, axis], kernel_size=kernel)
            data[:, axis] = sosfilt(self._sos, data[:, axis])
        return data


class RobustPreprocessor:
    """Coordinate normalization and amplitude normalization for robust
    gesture recognition across different wearing orientations and amplitudes.

    Processing steps:
    1. Gravity separation via complementary filter
    2. Coordinate frame alignment (gravity → canonical Z axis)
    3. Amplitude normalization (RMS-based scaling)

    This makes features invariant to:
    - Sensor wearing angle (clockwise/counterclockwise, 0°/45°/90°/135°/180°)
    - Gesture amplitude scaling (0.7×–1.3×)
    """

    def __init__(
            self,
            gravity_alpha: float = 0.98,
            calibration_frames: int = 10,
            target_accel_rms: float = 1000.0,
            target_gyro_rms: float = 500.0,
            enable_coordinate_norm: bool = True,
            enable_amplitude_norm: bool = True):
        self.gravity_alpha = float(gravity_alpha)
        self.calibration_frames = max(3, int(calibration_frames))
        self.target_accel_rms = float(target_accel_rms)
        self.target_gyro_rms = float(target_gyro_rms)
        self.enable_coordinate_norm = bool(enable_coordinate_norm)
        self.enable_amplitude_norm = bool(enable_amplitude_norm)

    def apply(self, filtered: np.ndarray) -> np.ndarray:
        """Preprocess an ``(N, 6)`` filtered IMU array.

        Returns ``(N, 6)`` with coordinate-normalized and amplitude-normalized
        data: [lin_ax, lin_ay, lin_az, gx, gy, gz].
        """
        data = np.asarray(filtered, dtype=np.float64)
        if data.ndim != 2 or data.shape[1] != 6:
            raise ValueError(
                f'Expected (N, 6) array, got {data.shape}')
        if len(data) == 0:
            return data.copy()

        accel = data[:, :3].copy()
        gyro = data[:, 3:6].copy()

        if self.enable_coordinate_norm:
            accel, gyro = self._normalize_coordinates(accel, gyro)

        if self.enable_amplitude_norm:
            accel, gyro = self._normalize_amplitude(accel, gyro)

        return np.hstack([accel, gyro])

    def _separate_gravity(
            self, accel: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Complementary filter gravity separation.

        Returns (linear_acceleration, gravity_vector_per_frame).
        """
        n = len(accel)
        gravity = np.zeros(3, dtype=np.float64)
        gravity_frames = np.zeros((n, 3), dtype=np.float64)
        alpha = self.gravity_alpha

        for i in range(n):
            gravity = alpha * gravity + (1.0 - alpha) * accel[i]
            gravity_frames[i] = gravity

        linear = accel - gravity_frames
        return linear, gravity_frames

    def _normalize_coordinates(
            self,
            accel: np.ndarray,
            gyro: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Align coordinate frame so Z axis points along gravity.

        Uses the average gravity direction from calibration frames to compute
        a rotation matrix (Rodrigues) that maps the observed gravity direction
        to the canonical [0, 0, -1] (sensor Z-up convention with gravity
        pulling down).
        """
        linear, gravity_frames = self._separate_gravity(accel)

        # Use calibration frames to estimate stable gravity direction
        cal_count = min(self.calibration_frames, len(gravity_frames))
        gravity_ref = gravity_frames[:cal_count].mean(axis=0)
        gravity_norm = np.linalg.norm(gravity_ref)

        if gravity_norm < 1e-6:
            # Cannot determine orientation; skip normalization
            return linear, gyro

        # Canonical direction: gravity should point along -Z (i.e. [0, 0, -g])
        gravity_dir = gravity_ref / gravity_norm
        target_dir = np.array([0.0, 0.0, -1.0])

        rotation = _rotation_between_vectors(gravity_dir, target_dir)

        # Apply rotation to all frames
        linear_rotated = (rotation @ linear.T).T
        gyro_rotated = (rotation @ gyro.T).T

        return linear_rotated, gyro_rotated

    def _normalize_amplitude(
            self,
            accel: np.ndarray,
            gyro: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Scale signals to target RMS amplitude.

        This ensures gestures performed at 0.7×–1.3× of training amplitude
        produce similar feature magnitudes.
        """
        accel_rms = float(np.sqrt(np.mean(accel ** 2)))
        if accel_rms > 1e-6:
            accel = accel * (self.target_accel_rms / accel_rms)

        gyro_rms = float(np.sqrt(np.mean(gyro ** 2)))
        if gyro_rms > 1e-6:
            gyro = gyro * (self.target_gyro_rms / gyro_rms)

        return accel, gyro


def _rotation_between_vectors(
        source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Compute the rotation matrix that rotates *source* direction to *target*.

    Uses Rodrigues' rotation formula.  Both inputs must be unit vectors.
    """
    v = np.cross(source, target)
    c = float(np.dot(source, target))
    s = float(np.linalg.norm(v))

    if s < 1e-8:
        # Vectors are (anti-)parallel
        if c > 0:
            return np.eye(3)
        # 180° rotation: find a perpendicular axis
        perp = (np.array([1.0, 0.0, 0.0])
                if abs(source[0]) < 0.9
                else np.array([0.0, 1.0, 0.0]))
        axis = np.cross(source, perp)
        axis /= np.linalg.norm(axis)
        # Rotation of π around axis: R = 2*outer(axis, axis) - I
        return 2.0 * np.outer(axis, axis) - np.eye(3)

    vx = np.array([
        [0.0, -v[2], v[1]],
        [v[2], 0.0, -v[0]],
        [-v[1], v[0], 0.0],
    ])
    rotation = np.eye(3) + vx + (vx @ vx) * ((1.0 - c) / (s * s))
    return rotation


def _augment_rotation(
        data: np.ndarray,
        max_angle_deg: float = 180.0) -> np.ndarray:
    """Apply a random rotation around the Z axis (gravity-aligned).

    This simulates different wearing angles around the finger.
    """
    angle = np.random.uniform(-max_angle_deg, max_angle_deg)
    return _apply_fixed_rotation(data, angle)


def _apply_fixed_rotation(data: np.ndarray, angle_deg: float) -> np.ndarray:
    """Apply a fixed rotation around the Z axis to (N, 6) IMU data."""
    theta = np.radians(angle_deg)
    cos_t, sin_t = np.cos(theta), np.sin(theta)
    rot_z = np.array([
        [cos_t, -sin_t, 0.0],
        [sin_t, cos_t, 0.0],
        [0.0, 0.0, 1.0],
    ])
    augmented = data.copy()
    augmented[:, :3] = (rot_z @ data[:, :3].T).T
    augmented[:, 3:6] = (rot_z @ data[:, 3:6].T).T
    return augmented


def _augment_tilt(
        data: np.ndarray,
        max_angle_deg: float = 30.0) -> np.ndarray:
    """Apply a random tilt rotation (around X or Y axis).

    This simulates the sensor being worn at a slight angle relative to the
    finger surface.
    """
    angle = np.random.uniform(-max_angle_deg, max_angle_deg)
    theta = np.radians(angle)
    cos_t, sin_t = np.cos(theta), np.sin(theta)
    # Randomly choose X or Y axis for tilt
    if np.random.random() < 0.5:
        rot = np.array([
            [1.0, 0.0, 0.0],
            [0.0, cos_t, -sin_t],
            [0.0, sin_t, cos_t],
        ])
    else:
        rot = np.array([
            [cos_t, 0.0, sin_t],
            [0.0, 1.0, 0.0],
            [-sin_t, 0.0, cos_t],
        ])
    augmented = data.copy()
    augmented[:, :3] = (rot @ data[:, :3].T).T
    augmented[:, 3:6] = (rot @ data[:, 3:6].T).T
    return augmented


def _augment_amplitude(
        data: np.ndarray,
        scale_range: tuple[float, float] = (0.7, 1.3)) -> np.ndarray:
    """Apply random amplitude scaling to simulate different gesture sizes."""
    scale = np.random.uniform(scale_range[0], scale_range[1])
    augmented = data.copy()
    augmented[:, :3] *= scale
    augmented[:, 3:6] *= scale
    return augmented


class FeatureExtractor:
    """Sliding-window mean/variance/RMS/zero-crossing features."""

    def __init__(self, window_size: int = 8, overlap: int = 4):
        self.window_size = max(2, int(window_size))
        self.overlap = max(0, min(int(overlap), self.window_size - 1))

    def extract(self, filtered: np.ndarray) -> np.ndarray:
        data = _as_imu_array(filtered).astype(np.float64, copy=False)
        step = self.window_size - self.overlap
        frames = []
        for start in range(0, len(data) - self.window_size + 1, step):
            window = data[start:start + self.window_size]
            mean = window.mean(axis=0)
            variance = window.var(axis=0)
            rms = np.sqrt(np.mean(window ** 2, axis=0))
            zcr = np.zeros(data.shape[1], dtype=np.float64)
            for axis in range(data.shape[1]):
                changes = np.diff(np.sign(window[:, axis]))
                zcr[axis] = np.count_nonzero(changes) / self.window_size
            frames.append(np.concatenate([mean, variance, rms, zcr]))
        if not frames:
            return np.empty((0, data.shape[1] * 4), dtype=np.float64)
        return np.asarray(frames, dtype=np.float64)


class MotionSegmenter:
    """Cut gesture segments from a continuous raw IMU stream."""

    IDLE = 0
    ACTIVE = 1
    TAIL = 2

    def __init__(
            self,
            energy_threshold: float = 900.0,
            min_onset_frames: int | None = None,
            min_offset_frames: int | None = None,
            min_gesture_len: int | None = None,
            max_gesture_len: int | None = None,
            pre_roll: int | None = None,
            cooldown_frames: int | None = None,
            sample_rate: float = 25.0):
        scale = max(0.25, float(sample_rate) / 25.0)

        def scaled(value: int, minimum: int = 1) -> int:
            return max(minimum, round(value * scale))

        self.energy_threshold = float(energy_threshold)
        self.min_onset_frames = int(
            min_onset_frames
            if min_onset_frames is not None else scaled(1))
        self.min_offset_frames = int(
            min_offset_frames
            if min_offset_frames is not None else scaled(3))
        self.min_gesture_len = int(
            min_gesture_len
            if min_gesture_len is not None else scaled(5))
        self.max_gesture_len = int(
            max_gesture_len
            if max_gesture_len is not None else scaled(88))
        self.pre_roll = int(
            pre_roll if pre_roll is not None else scaled(2))
        self.cooldown_frames = int(
            cooldown_frames
            if cooldown_frames is not None else scaled(5, minimum=0))
        self._baseline = np.zeros(6, dtype=np.float64)
        self._baseline_initialized = False
        self._previous_acceleration: np.ndarray | None = None
        self.reset(keep_baseline=True)

    def reset(self, keep_baseline: bool = False) -> None:
        self._state = self.IDLE
        self._onset_count = 0
        self._offset_count = 0
        self._buffer: list[list[int]] = []
        self._pre_buffer: list[list[int]] = []
        self._cooldown_remaining = 0
        if not keep_baseline:
            self._baseline[:] = 0.0
            self._baseline_initialized = False
            self._previous_acceleration = None

    def feed(self, samples: Sequence[Sequence[int]]) -> list[np.ndarray]:
        """Return every completed segment found in this batch."""
        results = []
        for sample in samples:
            result = self._feed_one([int(value) for value in sample])
            if result is not None:
                results.append(result)
        return results

    def _feed_one(self, sample: list[int]) -> np.ndarray | None:
        vector = np.asarray(sample, dtype=np.float64)
        if vector.shape != (6,):
            raise ValueError('each IMU sample must contain six values')

        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            self._update_baseline(vector)
            return None

        energy = self._compute_energy(vector)
        if self._state == self.IDLE:
            self._update_baseline(vector)
            self._pre_buffer.append(sample)
            if len(self._pre_buffer) > self.pre_roll:
                self._pre_buffer.pop(0)
            if energy > self.energy_threshold:
                self._onset_count += 1
                if self._onset_count >= self.min_onset_frames:
                    self._state = self.ACTIVE
                    self._buffer = list(self._pre_buffer)
                    self._pre_buffer = []
                    self._onset_count = 0
            else:
                self._onset_count = 0

        elif self._state == self.ACTIVE:
            self._buffer.append(sample)
            if energy < self.energy_threshold:
                self._offset_count += 1
                if self._offset_count >= self.min_offset_frames:
                    self._state = self.TAIL
            else:
                self._offset_count = 0
            if len(self._buffer) >= self.max_gesture_len:
                self._discard_and_cool_down()

        elif self._state == self.TAIL:
            segment = self._buffer[:len(self._buffer) - self._offset_count]
            self._discard_and_cool_down()
            if len(segment) >= self.min_gesture_len:
                return np.asarray(segment, dtype=np.int16)
        return None

    def _discard_and_cool_down(self) -> None:
        self._state = self.IDLE
        self._buffer = []
        self._offset_count = 0
        self._cooldown_remaining = self.cooldown_frames

    def _compute_energy(self, vector: np.ndarray) -> float:
        if not self._baseline_initialized:
            self._previous_acceleration = vector[:3].copy()
            return 0.0
        previous = self._previous_acceleration
        if previous is None:
            previous = vector[:3]
        acceleration_change = np.linalg.norm(vector[:3] - previous)
        gyro_motion = np.linalg.norm(vector[3:] - self._baseline[3:])
        self._previous_acceleration = vector[:3].copy()
        # Acceleration itself contains gravity and therefore changes when the
        # hand finishes in a different pose.  Its frame-to-frame delta and
        # gyro motion both fall back to zero when the hand becomes still.
        return float(gyro_motion + 2.0 * acceleration_change)

    def _update_baseline(self, vector: np.ndarray) -> None:
        if not self._baseline_initialized:
            self._baseline = vector.copy()
            self._baseline_initialized = True
        else:
            alpha = 0.02
            self._baseline = (
                (1.0 - alpha) * self._baseline + alpha * vector)
        self._previous_acceleration = vector[:3].copy()


@dataclass(frozen=True)
class GestureCandidate:
    """One model candidate with enough evidence for UI and language layers."""

    name: str
    raw_score: float
    adjusted_score: float
    threshold: float
    duration_penalty: float
    relative_probability: float
    absolute_fit: float
    confidence: float
    length_bounds: tuple[int, int] | None

    def to_payload(self) -> dict:
        return {
            'token': self.name,
            'raw_score': self.raw_score,
            'adjusted_score': self.adjusted_score,
            'threshold': self.threshold,
            'duration_penalty': self.duration_penalty,
            'relative_probability': self.relative_probability,
            'absolute_fit': self.absolute_fit,
            'confidence': self.confidence,
            'length_bounds': self.length_bounds,
        }


@dataclass(frozen=True)
class GestureDecision:
    """A three-state recognition event suitable for a future AI pipeline."""

    status: str
    reason: str
    segment_frames: int
    candidates: tuple[GestureCandidate, ...] = ()

    @property
    def best_candidate(self) -> GestureCandidate | None:
        return self.candidates[0] if self.candidates else None

    def to_payload(self) -> dict:
        return {
            'status': self.status,
            'reason': self.reason,
            'segment_frames': self.segment_frames,
            'candidates': [
                candidate.to_payload() for candidate in self.candidates
            ],
        }


class HMMRecognizer:
    """Load gesture models and classify segments from a live IMU stream."""

    def __init__(
            self,
            model_dir: str | Path,
            sample_rate: float = 25.0,
            cutoff_hz: float = 10.0,
            window_size: int = 8,
            window_overlap: int = 4,
        min_confidence: float = 0.0):
        self.model_dir = Path(model_dir)
        self.sample_rate = max(1.0, float(sample_rate))
        self.filter = SignalFilter(sample_rate, cutoff_hz)
        self.preprocessor = RobustPreprocessor()
        self.extractor = FeatureExtractor(window_size, window_overlap)
        self.min_confidence = float(min_confidence)
        self.segmenter = MotionSegmenter(sample_rate=sample_rate)
        self.enabled = True
        self._models: dict[str, object] = {}
        self.acceptance_thresholds: dict[str, float] = {}
        self.sample_length_bounds: dict[str, tuple[int, int]] = {}
        self.load_errors: list[str] = []
        self.rejected_segments = 0
        self.last_rejection: tuple[str, float, float] | None = None
        self.last_decision: GestureDecision | None = None
        self.reload_models()

    @property
    def model_names(self) -> tuple[str, ...]:
        return tuple(self._models)

    def reload_models(self) -> None:
        self._models = {}
        self.acceptance_thresholds = {}
        self.sample_length_bounds = {}
        self.load_errors = []
        self.rejected_segments = 0
        self.last_rejection = None
        self.last_decision = None
        self._model_versions: dict[str, int] = {}
        if not self.model_dir.exists():
            return
        for model_path in sorted(self.model_dir.glob('*.pkl')):
            try:
                with model_path.open('rb') as file:
                    model = pickle.load(file)
                self._models[model_path.stem] = model
                self._model_versions[model_path.stem] = int(
                    getattr(model, 'gesture_pipeline_version_', 2))
            except Exception as exc:
                self.load_errors.append(f'{model_path.name}: {exc}')
        self._calibrate_acceptance_thresholds()

    def _calibrate_acceptance_thresholds(self) -> None:
        """Load or derive a per-model absolute likelihood rejection gate."""
        derived = []
        pending = []
        incompatible = []
        for name, model in self._models.items():
            reference_rate = self._reference_sample_rate(name)
            model_rate = getattr(
                model, 'gesture_sample_rate_', reference_rate)
            if model_rate is not None:
                rate_ratio = float(model_rate) / self.sample_rate
                if not 0.8 <= rate_ratio <= 1.25:
                    self.load_errors.append(
                        f'{name}.pkl: 模型采样率 {float(model_rate):g}Hz '
                        f'与实时数据 {self.sample_rate:g}Hz 不兼容，已忽略')
                    incompatible.append(name)
                    continue

            reference_repetitions = self._reference_repetitions(name)
            stored_bounds = getattr(
                model, 'gesture_sample_length_bounds_', None)
            if (
                    stored_bounds is not None
                    and len(stored_bounds) == 2):
                self.sample_length_bounds[name] = (
                    int(stored_bounds[0]), int(stored_bounds[1]))
            elif reference_repetitions:
                lengths = np.asarray(
                    [len(item) for item in reference_repetitions],
                    dtype=np.float64,
                )
                median_length = float(np.median(lengths))
                self.sample_length_bounds[name] = (
                    max(8, round(median_length * 0.4)),
                    max(12, round(median_length * 2.0)),
                )

            stored = getattr(
                model, 'gesture_acceptance_threshold_', None)
            if stored is not None and np.isfinite(stored):
                threshold = float(stored)
                self.acceptance_thresholds[name] = threshold
                derived.append(threshold)
                continue

            scores = []
            version = self._model_versions.get(name, 2)
            for repetition in reference_repetitions:
                filtered_rep = self.filter.apply(repetition)
                if version >= 3:
                    features = self.extractor.extract(
                        self.preprocessor.apply(filtered_rep))
                else:
                    features = self.extractor.extract(filtered_rep)
                if len(features) < 2:
                    continue
                try:
                    scores.append(
                        float(model.score(features)) / len(features))
                except Exception:
                    continue
            if scores:
                threshold = _acceptance_threshold(scores)
                self.acceptance_thresholds[name] = threshold
                derived.append(threshold)
            else:
                pending.append(name)

        # Imported legacy models may not carry training metadata.  Use the
        # median gate from models that can be calibrated instead of disabling
        # rejection entirely.
        fallback = float(np.median(derived)) if derived else -250.0
        for name in pending:
            self.acceptance_thresholds[name] = fallback
        for name in incompatible:
            self._models.pop(name, None)
            self.acceptance_thresholds.pop(name, None)
            self.sample_length_bounds.pop(name, None)

    def _reference_repetitions(self, name: str) -> list[np.ndarray]:
        for directory_name in ('gesture_data', 'sample_data'):
            path = self.model_dir.parent / directory_name / f'{name}.json'
            if not path.exists():
                continue
            try:
                _, _, repetitions = load_gesture_data(path)
                return repetitions
            except Exception:
                continue
        return []

    def _reference_sample_rate(self, name: str) -> float | None:
        for directory_name in ('gesture_data', 'sample_data'):
            path = self.model_dir.parent / directory_name / f'{name}.json'
            if not path.exists():
                continue
            try:
                _, sample_rate, _ = load_gesture_data(path)
                return float(sample_rate)
            except Exception:
                continue
        return None

    def feed(
            self,
            samples: Sequence[Sequence[int]]) -> list[tuple[str, float]]:
        decisions = self.feed_decisions(samples)
        return [
            (decision.best_candidate.name, decision.best_candidate.confidence)
            for decision in decisions
            if (
                decision.status == 'confirmed'
                and decision.best_candidate is not None)
        ]

    def feed_decisions(
            self,
            samples: Sequence[Sequence[int]]) -> list[GestureDecision]:
        if not self.enabled or not self._models:
            return []
        decisions = []
        for segment in self.segmenter.feed(samples):
            decision = self.analyze_segment(segment)
            decisions.append(decision)
            self.last_decision = decision
            if decision.status == 'rejected':
                self.rejected_segments += 1
        return decisions

    def classify_segment(
            self, segment: np.ndarray) -> tuple[str, float] | None:
        decision = self.analyze_segment(segment)
        self.last_decision = decision
        candidate = decision.best_candidate
        if decision.status != 'confirmed' or candidate is None:
            return None
        return candidate.name, candidate.confidence

    def analyze_segment(self, segment: np.ndarray) -> GestureDecision:
        segment = _as_imu_array(segment)
        filtered = self.filter.apply(segment)
        # Compute both feature sets for backward compatibility
        features_v3 = self.extractor.extract(
            self.preprocessor.apply(filtered))
        features_v2 = self.extractor.extract(filtered)
        if len(features_v3) < 2 and len(features_v2) < 2:
            self.last_rejection = None
            return GestureDecision(
                'rejected', 'segment_too_short', len(segment))

        scored = []
        for name, model in self._models.items():
            # Select features matching the model's training pipeline version
            version = self._model_versions.get(name, 2)
            features = features_v3 if version >= 3 else features_v2
            if len(features) < 2:
                continue
            bounds = self.sample_length_bounds.get(name)
            duration_penalty = 0.0
            if bounds is not None:
                minimum_length, maximum_length = bounds
                if len(segment) < minimum_length:
                    duration_ratio = minimum_length / max(1, len(segment))
                elif len(segment) > maximum_length:
                    duration_ratio = len(segment) / maximum_length
                else:
                    duration_ratio = 1.0
                # Moderate timing differences should reduce confidence rather
                # than erase an otherwise useful candidate.  Only obviously
                # incompatible durations are excluded.
                if duration_ratio > 3.0:
                    continue
                duration_penalty = 12.0 * float(np.log(duration_ratio))
            try:
                normalized_score = (
                    float(model.score(features)) / len(features))
                scored.append({
                    'name': name,
                    'raw_score': normalized_score,
                    'adjusted_score': normalized_score - duration_penalty,
                    'threshold': self.acceptance_thresholds.get(
                        name, -250.0),
                    'duration_penalty': duration_penalty,
                    'length_bounds': bounds,
                })
            except Exception:
                continue
        if not scored:
            self.last_rejection = None
            return GestureDecision(
                'rejected', 'duration_outlier', len(segment))

        scored.sort(
            key=lambda item: item['adjusted_score'], reverse=True)
        score_values = np.asarray(
            [item['adjusted_score'] for item in scored],
            dtype=np.float64,
        )
        weights = np.exp(
            np.clip((score_values - score_values[0]) / 10.0, -60.0, 0.0))
        relative_probabilities = weights / weights.sum()

        candidates = []
        for item, relative_probability in zip(
                scored[:3], relative_probabilities[:3]):
            absolute_margin = (
                item['adjusted_score'] - item['threshold'])
            absolute_fit = 1.0 / (
                1.0 + float(np.exp(
                    -np.clip(absolute_margin / 12.0, -60.0, 60.0))))
            confidence = float(np.sqrt(
                absolute_fit * float(relative_probability)))
            candidates.append(GestureCandidate(
                name=item['name'],
                raw_score=float(item['raw_score']),
                adjusted_score=float(item['adjusted_score']),
                threshold=float(item['threshold']),
                duration_penalty=float(item['duration_penalty']),
                relative_probability=float(relative_probability),
                absolute_fit=float(absolute_fit),
                confidence=min(0.999, max(0.0, confidence)),
                length_bounds=item['length_bounds'],
            ))

        best = candidates[0]
        absolute_margin = best.adjusted_score - best.threshold
        if absolute_margin >= 0.0 and best.confidence >= 0.75:
            status = 'confirmed'
            reason = 'accepted'
            self.last_rejection = None
        elif (
                absolute_margin >= -25.0
                and best.confidence >= max(0.10, self.min_confidence)):
            status = 'tentative'
            reason = (
                'ambiguous'
                if best.relative_probability < 0.75
                else 'near_threshold')
            self.last_rejection = (
                best.name, best.adjusted_score, best.threshold)
        else:
            status = 'rejected'
            reason = (
                'ambiguous'
                if best.absolute_fit >= 0.35
                else 'low_absolute_fit')
            self.last_rejection = (
                best.name, best.adjusted_score, best.threshold)
        return GestureDecision(
            status, reason, len(segment), tuple(candidates))


@dataclass(frozen=True)
class TrainingResult:
    trained: tuple[str, ...]
    failed: tuple[str, ...]


def _as_imu_array(data: np.ndarray | Sequence[Sequence[int]]) -> np.ndarray:
    array = np.asarray(data)
    if array.size == 0:
        return np.empty((0, 6), dtype=array.dtype or np.float64)
    if array.ndim != 2 or array.shape[1] != 6:
        raise ValueError(
            f'IMU data must have shape (N, 6), got {array.shape}')
    return array


def _safe_gesture_name(name: str) -> str:
    cleaned = str(name).strip().replace('/', '_').replace('\\', '_')
    cleaned = ''.join(
        character for character in cleaned
        if character not in '<>:"|?*' and ord(character) >= 32)
    return cleaned[:64] or '未命名手势'


def save_gesture(
        name: str,
        repetitions: Iterable[np.ndarray | Sequence[Sequence[int]]],
        output_dir: str | Path,
        sample_rate_hz: float = 25.0) -> Path:
    """Persist one gesture and its repetitions in the SDK JSON format."""
    arrays = [
        _as_imu_array(repetition).astype(np.int16)
        for repetition in repetitions
    ]
    if len(arrays) < 2:
        raise ValueError('至少需要两次有效录制')
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_gesture_name(name)
    timezone_cn = timezone(timedelta(hours=8))
    payload = {
        'name': str(name).strip() or safe_name,
        'created_at': datetime.now(timezone_cn).isoformat(),
        'sample_rate_hz': float(sample_rate_hz),
        'num_repetitions': len(arrays),
        'repetitions': [
            {
                'index': index,
                'num_samples': len(array),
                'data': array.tolist(),
            }
            for index, array in enumerate(arrays)
        ],
    }
    path = output / f'{safe_name}.json'
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return path


def load_gesture_data(
        path: str | Path) -> tuple[str, float, list[np.ndarray]]:
    source = Path(path)
    payload = json.loads(source.read_text(encoding='utf-8'))
    name = str(payload['name'])
    sample_rate = float(payload.get('sample_rate_hz', 25.0))
    repetitions = [
        _as_imu_array(repetition['data']).astype(np.int16)
        for repetition in payload['repetitions']
    ]
    return name, sample_rate, repetitions


def _build_left_right_hmm(
        n_states: int,
        features: np.ndarray,
        lengths: list[int]) -> hmm.GaussianHMM:
    model = hmm.GaussianHMM(
        n_components=n_states,
        covariance_type='diag',
        n_iter=100,
        tol=1e-4,
        init_params='',
        params='mc',
    )
    start_probability = np.zeros(n_states)
    start_probability[0] = 1.0
    model.startprob_ = start_probability

    transition = np.zeros((n_states, n_states))
    for state in range(n_states):
        if state < n_states - 1:
            transition[state, state] = 0.7
            transition[state, state + 1] = 0.3
        else:
            transition[state, state] = 1.0
    model.transmat_ = transition

    n_features = features.shape[1]
    means = np.zeros((n_states, n_features))
    covariances = np.zeros((n_states, n_features))
    state_data: list[list[np.ndarray]] = [[] for _ in range(n_states)]
    offset = 0
    for length in lengths:
        sequence = features[offset:offset + length]
        segment_size = length / n_states
        for frame_index in range(length):
            state = min(int(frame_index / segment_size), n_states - 1)
            state_data[state].append(sequence[frame_index])
        offset += length
    for state, frames in enumerate(state_data):
        state_frames = np.asarray(frames)
        means[state] = state_frames.mean(axis=0)
        covariances[state] = state_frames.var(axis=0) + 1e-2
    model.means_ = means
    model.covars_ = covariances
    return model


def _acceptance_threshold(scores: Sequence[float]) -> float:
    values = np.asarray(scores, dtype=np.float64)
    if not len(values):
        return -250.0
    spread = float(values.std())
    margin = max(20.0, 2.5 * spread)
    return float(np.percentile(values, 5.0) - margin)


def _segment_training_repetitions(
        repetitions: Sequence[np.ndarray],
        sample_rate: float) -> list[np.ndarray]:
    """Extract the live-equivalent motion region from manual recordings."""
    segmented: list[np.ndarray] = []
    for repetition in repetitions:
        array = _as_imu_array(repetition).astype(np.int16)
        if not len(array):
            continue
        segmenter = MotionSegmenter(
            sample_rate=sample_rate,
            cooldown_frames=0,
        )
        candidates = segmenter.feed(array)
        if segmenter._state != MotionSegmenter.IDLE:
            flush_count = segmenter.min_offset_frames + 2
            candidates.extend(segmenter.feed(
                [array[-1].tolist()] * flush_count))
        if candidates:
            longest = max(candidates, key=len)
            if len(longest) >= 12:
                segmented.append(longest)

    # Do not mix cropped and uncropped recordings.  If the recordings do not
    # contain enough detectable motion (for example an imported legacy data
    # set that starts directly on the first action frame), preserve the SDK's
    # original full-recording behavior.
    if len(segmented) >= 2:
        return segmented
    return [
        _as_imu_array(repetition).astype(np.int16)
        for repetition in repetitions
        if len(repetition)
    ]


def _train_gesture(
        repetitions: Sequence[np.ndarray],
        sample_rate: float,
        n_states: int,
        cutoff_hz: float,
        window_size: int,
        window_overlap: int) -> hmm.GaussianHMM | None:
    signal_filter = SignalFilter(sample_rate, cutoff_hz)
    preprocessor = RobustPreprocessor()
    extractor = FeatureExtractor(window_size, window_overlap)
    training_repetitions = _segment_training_repetitions(
        repetitions, sample_rate)

    # --- Data augmentation for orientation & amplitude robustness ---
    augmented_repetitions = list(training_repetitions)
    for repetition in training_repetitions:
        rep_f = repetition.astype(np.float64)
        # Z-axis rotations (simulates different wearing angles around finger)
        for angle in (45.0, 90.0, 135.0, 180.0, -45.0, -90.0, -135.0):
            augmented_repetitions.append(
                _apply_fixed_rotation(rep_f, angle))
        # Amplitude scaling (simulates different gesture sizes)
        for scale in (0.7, 0.85, 1.15, 1.3):
            augmented_repetitions.append(rep_f * scale)
        # Combined tilt + rotation
        augmented_repetitions.append(
            _augment_tilt(rep_f, max_angle_deg=20.0))

    # Deduplicate while keeping augmented data manageable
    if len(augmented_repetitions) > len(training_repetitions) * 12:
        augmented_repetitions = augmented_repetitions[
            :len(training_repetitions) * 12]

    sequences = []
    lengths = []
    for repetition in augmented_repetitions:
        rep_array = np.asarray(repetition, dtype=np.float64)
        if rep_array.ndim != 2 or rep_array.shape[1] != 6:
            continue
        filtered = signal_filter.apply(rep_array)
        preprocessed = preprocessor.apply(filtered)
        features = extractor.extract(preprocessed)
        if len(features) < 2:
            continue
        sequences.append(features)
        lengths.append(len(features))
    if len(sequences) < 2:
        return None

    all_features = np.concatenate(sequences)
    actual_states = min(
        int(n_states),
        max(2, min(lengths) // 3),
    )
    model = _build_left_right_hmm(
        actual_states, all_features, lengths)
    model.fit(all_features, lengths)
    zero_rows = model.transmat_.sum(axis=1) == 0
    for state in np.where(zero_rows)[0]:
        model.transmat_[state, state] = 1.0
    normalized_scores = []
    for sequence in sequences:
        normalized_scores.append(
            float(model.score(sequence)) / len(sequence))
    model.gesture_training_scores_ = tuple(normalized_scores)
    model.gesture_acceptance_threshold_ = _acceptance_threshold(
        normalized_scores)
    raw_lengths = [len(repetition) for repetition in training_repetitions]
    model.gesture_sample_length_bounds_ = (
        max(8, round(min(raw_lengths) * 0.5)),
        max(12, round(max(raw_lengths) * 1.75)),
    )
    model.gesture_pipeline_version_ = 3
    model.gesture_sample_rate_ = float(sample_rate)
    return model


def train_directory(
        data_dir: str | Path,
        output_dir: str | Path,
        *,
        n_states: int = 6,
        cutoff_hz: float = 10.0,
        window_size: int = 8,
        window_overlap: int = 4,
        progress: Callable[[str], None] | None = None) -> TrainingResult:
    """Train every gesture JSON in ``data_dir`` into ``output_dir``."""
    source = Path(data_dir)
    target = Path(output_dir)
    files = sorted(source.glob('*.json')) if source.exists() else []
    if not files:
        raise ValueError('没有可训练的手势数据')
    target.mkdir(parents=True, exist_ok=True)

    trained = []
    failed = []
    for path in files:
        try:
            name, sample_rate, repetitions = load_gesture_data(path)
            if progress:
                progress(f'正在训练「{name}」：{len(repetitions)} 次录制')
            model = _train_gesture(
                repetitions,
                sample_rate,
                n_states,
                cutoff_hz,
                window_size,
                window_overlap,
            )
            if model is None:
                failed.append(name)
                if progress:
                    progress(f'跳过「{name}」：有效录制不足')
                continue
            destination = target / f'{path.stem}.pkl'
            temporary = destination.with_suffix('.pkl.tmp')
            with temporary.open('wb') as file:
                pickle.dump(model, file)
            temporary.replace(destination)
            trained.append(name)
            if progress:
                progress(f'已完成「{name}」')
        except Exception as exc:
            failed.append(path.stem)
            if progress:
                progress(f'训练「{path.stem}」失败：{exc}')
    return TrainingResult(tuple(trained), tuple(failed))
