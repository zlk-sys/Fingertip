# coding: utf-8
"""Six-axis orientation estimation for the trajectory engine."""

import math

import numpy as np


def rotation_matrix(rotation_vector):
    """Convert an axis-angle rotation vector to a 3x3 rotation matrix."""
    vector = np.asarray(rotation_vector, dtype=np.float64)
    angle = float(np.linalg.norm(vector))
    if angle < 1e-12:
        x, y, z = vector
        return np.array([
            [1.0, -z, y],
            [z, 1.0, -x],
            [-y, x, 1.0],
        ], dtype=np.float64)

    x, y, z = vector / angle
    skew = np.array([
        [0.0, -z, y],
        [z, 0.0, -x],
        [-y, x, 0.0],
    ], dtype=np.float64)
    return (np.eye(3, dtype=np.float64)
            + math.sin(angle) * skew
            + (1.0 - math.cos(angle)) * (skew @ skew))


class OrientationFilter:
    """Body-to-reference orientation with accelerometer tilt correction."""

    def __init__(
            self,
            moving_correction_rate=0.45,
            stationary_correction_rate=3.5,
            accel_tolerance_g=0.18):
        self.moving_correction_rate = float(moving_correction_rate)
        self.stationary_correction_rate = float(stationary_correction_rate)
        self.accel_tolerance_g = float(accel_tolerance_g)
        self.rotation = np.eye(3, dtype=np.float64)
        self.initialized = False
        self._updates = 0

    def reset(self, accel_g=None):
        self.rotation = np.eye(3, dtype=np.float64)
        self.initialized = False
        self._updates = 0
        if accel_g is None:
            return False

        gravity_body = np.asarray(accel_g, dtype=np.float64)
        norm = float(np.linalg.norm(gravity_body))
        if norm < 1e-6:
            return False
        gravity_body /= norm

        # Gravity determines two attitude degrees of freedom.  The remaining
        # heading is seeded deterministically and later resolved by the
        # guided right/up wearing calibration.
        seed = np.array([1.0, 0.0, 0.0])
        x_axis = seed - np.dot(seed, gravity_body) * gravity_body
        if float(np.linalg.norm(x_axis)) < 0.2:
            seed = np.array([0.0, 1.0, 0.0])
            x_axis = seed - np.dot(seed, gravity_body) * gravity_body
        x_axis /= np.linalg.norm(x_axis)
        y_axis = np.cross(gravity_body, x_axis)
        y_axis /= np.linalg.norm(y_axis)

        # Rows are reference axes expressed in body coordinates.
        self.rotation = np.vstack([x_axis, y_axis, gravity_body])
        self.initialized = True
        return True

    def body_to_reference(self, vector):
        return self.rotation @ np.asarray(vector, dtype=np.float64)

    def update(self, gyro_dps, accel_g, dt, stationary=False):
        if not self.initialized:
            self.reset(accel_g)
            return
        if dt <= 0.0:
            return

        body_delta = np.deg2rad(
            np.asarray(gyro_dps, dtype=np.float64)) * dt
        self.rotation = self.rotation @ rotation_matrix(body_delta)

        acceleration = np.asarray(accel_g, dtype=np.float64)
        accel_norm = float(np.linalg.norm(acceleration))
        if (accel_norm > 1e-6
                and abs(accel_norm - 1.0) <= self.accel_tolerance_g):
            measured_ref = self.rotation @ (acceleration / accel_norm)
            target_ref = np.array([0.0, 0.0, 1.0])
            cross = np.cross(measured_ref, target_ref)
            sine = float(np.linalg.norm(cross))
            if sine > 1e-9:
                cosine = float(np.clip(
                    np.dot(measured_ref, target_ref), -1.0, 1.0))
                angle = math.atan2(sine, cosine)
                rate = (self.stationary_correction_rate if stationary
                        else self.moving_correction_rate)
                correction = cross / sine * angle * min(1.0, rate * dt)
                self.rotation = (
                    rotation_matrix(correction) @ self.rotation)

        self._updates += 1
        if self._updates % 128 == 0:
            left, _, right = np.linalg.svd(self.rotation)
            self.rotation = left @ right
            if np.linalg.det(self.rotation) < 0.0:
                left[:, -1] *= -1.0
                self.rotation = left @ right
