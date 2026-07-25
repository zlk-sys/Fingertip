#!/usr/bin/env python3
"""
戒指手势识别 v3.4 - 高精度HMM + 14维特征 + 数据预处理管线

改进点:
  - 特征从7维扩充到14维 (加jerk/gyro_z/倾斜角/短时能量)
  - HMM用full协方差矩阵捕捉特征间关联
  - 训练30次 + 多初始化取最优 + 数据增强
  - 识别分数按序列长度归一化 + 重叠窗口
  - 预处理管线: 低通滤波 → 3σ异常剔除 → 重力分离 → 坐标归一化
"""

import asyncio
import json
import pickle
import time
import numpy as np
from collections import deque
from pathlib import Path
from typing import Optional, List, Dict

try:
    from .sdk import ring_sound as sdk  # python -m app.train
except ImportError:
    import sdk.ring_sound as sdk  # cd app && python train.py

try:
    from hmmlearn import hmm
except ImportError:
    print("请安装: pip install hmmlearn")
    hmm = None

# 配置
MAC_ADDRESS = "C6:AE:FB:38:18:DF"
SAMPLE_DURATION = 2.0
TRAIN_EPOCHS = 30           # 每个动作训练30次 (平衡点)
WINDOW_SIZE = 30
N_STATES = 8                 # HMM状态数 (更多状态=更精细建模)
MATCH_THRESHOLD = -500       # 对数似然阈值 (14维特征下需调整)
SILENCE_MARGIN = 80          # 与静默模型的分数差距要求
MODEL_FILE = Path("gesture_models_v3.pkl")
MODEL_VERSION = "3.4"

# ==================== 预处理管线配置 ====================
LPF_ALPHA_ACCEL = 0.3       # 加速度计低通滤波系数 (0.2~0.5, 越小越平滑)
LPF_ALPHA_GYRO = 0.5        # 陀螺仪低通滤波系数 (稍高, 保留快速旋转)
OUTLIER_WINDOW = 10          # 异常值检测局部窗口大小
OUTLIER_SIGMA = 3.0          # 异常值阈值 (3倍标准差)
GRAVITY_ALPHA = 0.98         # 互补滤波器系数 (越大越信任历史重力估计)
COORD_NORMALIZE = True       # 是否做坐标系归一化 (佩戴方向不固定时开启)


class GestureTrainer:
    def __init__(self):
        self.gestures: Dict[str, hmm.GaussianHMM] = {}
        self.silence_model: Optional[hmm.GaussianHMM] = None
        self.ring = None
        self.is_connected = False
        self.mac = MAC_ADDRESS
        self._load_models()          # 启动时自动加载已有模型

    # ==================== 数据预处理管线 ====================
    
    def _lowpass_filter(self, data: np.ndarray, alpha: float) -> np.ndarray:
        """
        一阶IIR低通滤波器, 滤除高频抖动噪声。
        y(t) = α·x(t) + (1-α)·y(t-1)
        α 越小 → 截止频率越低 → 越平滑
        """
        n = len(data)
        if n < 2:
            return data.copy()
        filtered = np.empty(n, dtype=np.float64)
        filtered[0] = data[0]
        for i in range(1, n):
            filtered[i] = alpha * data[i] + (1.0 - alpha) * filtered[i - 1]
        return filtered
    
    def _remove_outliers(self, data: np.ndarray,
                         window: int = OUTLIER_WINDOW,
                         sigma: float = OUTLIER_SIGMA) -> np.ndarray:
        """
        三倍标准差法剔除异常值。
        在局部窗口内, 超过 μ±3σ 的点用窗口中位数替换。
        """
        n = len(data)
        if n < 3:
            return data.copy()
        cleaned = data.copy()
        half_w = window // 2
        for i in range(n):
            lo = max(0, i - half_w)
            hi = min(n, i + half_w + 1)
            local = data[lo:hi]
            mu = np.mean(local)
            std = np.std(local)
            if std < 1e-8:
                continue
            if abs(data[i] - mu) > sigma * std:
                cleaned[i] = np.median(local)
        return cleaned
    
    def _separate_gravity(self, ax: np.ndarray, ay: np.ndarray, az: np.ndarray
                          ) -> tuple:
        """
        互补滤波器分离重力分量和线性加速度。
        gravity(t) = α·gravity(t-1) + (1-α)·accel(t)
        linear_accel = accel - gravity
        α=0.98 表示重力估计主要依赖历史值(低通), 线性加速度保留高频。
        """
        n = len(ax)
        grav_x = np.zeros(n)
        grav_y = np.zeros(n)
        grav_z = np.zeros(n)
    
        grav_x[0] = ax[0]
        grav_y[0] = ay[0]
        grav_z[0] = az[0]
    
        alpha = GRAVITY_ALPHA
        for i in range(1, n):
            grav_x[i] = alpha * grav_x[i - 1] + (1.0 - alpha) * ax[i]
            grav_y[i] = alpha * grav_y[i - 1] + (1.0 - alpha) * ay[i]
            grav_z[i] = alpha * grav_z[i - 1] + (1.0 - alpha) * az[i]
    
        lin_x = ax - grav_x
        lin_y = ay - grav_y
        lin_z = az - grav_z
    
        return lin_x, lin_y, lin_z, grav_x, grav_y, grav_z
    
    def _normalize_coordinate(self, ax: np.ndarray, ay: np.ndarray, az: np.ndarray
                              ) -> tuple:
        """
        坐标系归一化: 用前N帧平均加速度方向(≈重力方向)计算旋转矩阵,
        将数据统一到参考坐标系 (Z轴对齐重力方向)。
        解决设备佩戴方向不固定的问题。
        """
        n_ref = min(10, len(ax))
        ref = np.array([
            np.mean(ax[:n_ref]),
            np.mean(ay[:n_ref]),
            np.mean(az[:n_ref])
        ])
        ref_norm = np.linalg.norm(ref)
        if ref_norm < 1e-6:
            return ax, ay, az
    
        ref_unit = ref / ref_norm
        target = np.array([0.0, 0.0, 1.0])
    
        v = np.cross(ref_unit, target)
        s = np.linalg.norm(v)
        c = np.dot(ref_unit, target)
    
        if s < 1e-6:
            if c > 0:
                R = np.eye(3)
            else:
                R = np.diag([-1.0, -1.0, 1.0])
        else:
            vx = np.array([
                [0, -v[2], v[1]],
                [v[2], 0, -v[0]],
                [-v[1], v[0], 0]
            ])
            R = np.eye(3) + vx + vx @ vx * ((1.0 - c) / (s * s))
    
        data = np.column_stack([ax, ay, az])
        rotated = data @ R.T
        return rotated[:, 0], rotated[:, 1], rotated[:, 2]
    
    def _preprocess_raw(self, ax, ay, az, gx, gy, gz):
        """
        完整预处理管线 (训练和识别共用):
          Step 1: 一阶低通滤波去噪
          Step 2: 3σ异常值剔除
          Step 3: 互补滤波器重力分离
          Step 4: 坐标系归一化 (Rodrigues旋转)
        返回: (线性加速度x/y/z, 陀螺仪x/y/z, 重力x/y/z)
        """
        # Step 1: 低通滤波
        ax = self._lowpass_filter(ax, LPF_ALPHA_ACCEL)
        ay = self._lowpass_filter(ay, LPF_ALPHA_ACCEL)
        az = self._lowpass_filter(az, LPF_ALPHA_ACCEL)
        gx = self._lowpass_filter(gx, LPF_ALPHA_GYRO)
        gy = self._lowpass_filter(gy, LPF_ALPHA_GYRO)
        gz = self._lowpass_filter(gz, LPF_ALPHA_GYRO)
    
        # Step 2: 异常值剔除
        ax = self._remove_outliers(ax)
        ay = self._remove_outliers(ay)
        az = self._remove_outliers(az)
        gx = self._remove_outliers(gx)
        gy = self._remove_outliers(gy)
        gz = self._remove_outliers(gz)
    
        # Step 3: 重力分离
        lin_x, lin_y, lin_z, grav_x, grav_y, grav_z = self._separate_gravity(ax, ay, az)
    
        # Step 4: 坐标系归一化
        if COORD_NORMALIZE:
            lin_x, lin_y, lin_z = self._normalize_coordinate(lin_x, lin_y, lin_z)
    
        return lin_x, lin_y, lin_z, gx, gy, gz, grav_x, grav_y, grav_z
    
    # ==================== 核心：14维高区分度特征提取 ====================
    
    def _extract_features(self, samples: List[Dict]) -> np.ndarray:
        """
        提取14维特征 (输入经过预处理管线清洗):
    
        [0]  mag            - 线性合加速度大小 (去重力后)
        [1-3] ax/ay/az_norm - 归一化方向向量 (三维)
        [4]   dir_change    - 方向变化率 (画圆大, 直线小)
        [5]   angle_change  - 相邻帧角度变化
        [6-8] gx/gy/gz      - 三轴陀螺仪
        [9]   gyro_mag      - 陀螺仪模长
        [10]  jerk          - 加速度急动度 (平滑vs急促)
        [11]  energy        - 短时能量 (动作幅度大小)
        [12]  tilt_x        - X轴倾斜角 (from重力方向)
        [13]  tilt_y        - Y轴倾斜角 (from重力方向)
        """
        if len(samples) < 3:
            return np.array([])
    
        # ---- 原始数据归一化 ----
        ax = np.array([s['accel_x'] for s in samples]) / 2048.0
        ay = np.array([s['accel_y'] for s in samples]) / 2048.0
        az = np.array([s['accel_z'] for s in samples]) / 2048.0
        gx = np.array([s['gyro_x'] for s in samples]) / 2000.0
        gy = np.array([s['gyro_y'] for s in samples]) / 2000.0
        gz = np.array([s['gyro_z'] for s in samples]) / 2000.0
    
        # ---- ★ 预处理管线: 去噪 → 异常剔除 → 重力分离 → 坐标归一化 ----
        lin_x, lin_y, lin_z, gx, gy, gz, grav_x, grav_y, grav_z = \
            self._preprocess_raw(ax, ay, az, gx, gy, gz)
    
        # ---- 合加速度 (线性, 已去重力) ----
        mag = np.sqrt(lin_x ** 2 + lin_y ** 2 + lin_z ** 2)
    
        # ---- 归一化方向向量 ----
        mag_safe = mag + 0.0001
        ax_norm = lin_x / mag_safe
        ay_norm = lin_y / mag_safe
        az_norm = lin_z / mag_safe
    
        # ---- 方向变化率 ----
        dx = np.gradient(ax_norm)
        dy = np.gradient(ay_norm)
        dz = np.gradient(az_norm)
        dir_change = np.sqrt(dx ** 2 + dy ** 2 + dz ** 2)
    
        # ---- 角度变化 ----
        angle_change = np.zeros(len(ax_norm))
        for i in range(1, len(ax_norm)):
            dot = (ax_norm[i] * ax_norm[i - 1] +
                   ay_norm[i] * ay_norm[i - 1] +
                   az_norm[i] * az_norm[i - 1])
            dot = np.clip(dot, -1.0, 1.0)
            angle_change[i] = np.arccos(dot)
    
        # ---- 陀螺仪模长 ----
        gyro_mag = np.sqrt(gx ** 2 + gy ** 2 + gz ** 2)
    
        # ---- 急动度 jerk = 线性加速度模长的导数 ----
        jerk = np.gradient(mag)
    
        # ---- 短时能量 = 滑动窗内加速度模长的方差 ----
        win = min(5, len(mag))
        energy = np.zeros(len(mag))
        for i in range(len(mag)):
            lo = max(0, i - win // 2)
            hi = min(len(mag), i + win // 2 + 1)
            energy[i] = np.var(mag[lo:hi]) if hi > lo + 1 else 0.0
    
        # ---- 倾斜角 from 重力方向 (而非原始加速度, 更稳定) ----
        grav_mag_safe = np.sqrt(grav_x ** 2 + grav_y ** 2 + grav_z ** 2) + 0.0001
        tilt_x = np.arctan2(grav_x, np.sqrt(grav_y ** 2 + grav_z ** 2))
        tilt_y = np.arctan2(grav_y, np.sqrt(grav_x ** 2 + grav_z ** 2))
    
        # ---- 拼成14维特征矩阵 ----
        features = np.column_stack([
            mag,               # [0]
            ax_norm,           # [1]
            ay_norm,           # [2]
            az_norm,           # [3]
            dir_change,        # [4]
            angle_change,      # [5]
            gx,                # [6]
            gy,                # [7]
            gz,                # [8]
            gyro_mag,          # [9]
            jerk,              # [10]
            energy,            # [11]
            tilt_x,            # [12]
            tilt_y,            # [13]
        ])
    
        features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)
    
        return features

    # ==================== 训练 (改进版) ====================

    def _augment_samples(self, features: np.ndarray, noise_level: float = 0.005) -> np.ndarray:
        """数据增强: 加微小高斯噪声, 提升泛化能力。"""
        noise = np.random.randn(*features.shape) * noise_level
        return features + noise

    def _train_hmm(self, name: str, samples_list: List[List[Dict]]) -> bool:
        if hmm is None:
            return False

        # ---- 提取特征 ----
        all_features = []
        lengths = []
        for samples in samples_list:
            features = self._extract_features(samples)
            if len(features) > 10:
                all_features.append(features)
                lengths.append(len(features))

        if len(all_features) < 3:
            print("   ❌ 有效样本不足")
            return False

        X = np.vstack(all_features)
        lengths = np.array(lengths)
        n_features = X.shape[1]  # 14

        # ---- 数据增强: 每个样本衍生2个噪声副本 ----
        augmented_X = [X]
        augmented_lengths = [lengths.copy()]
        for _ in range(2):
            aug = self._augment_samples(X)
            augmented_X.append(aug)
            augmented_lengths.append(lengths.copy())
        X_total = np.vstack(augmented_X)
        lengths_total = np.concatenate(augmented_lengths)

        # ---- 标准化特征 ----
        mean = X_total.mean(axis=0)
        std = X_total.std(axis=0) + 0.001
        X_norm = (X_total - mean) / std

        # ---- 多次随机初始化, 取最优 ----
        best_model = None
        best_score = -float('inf')
        n_tries = 2           # 减少重启次数(2-3 足矣, 5 太慢)

        import time as _time
        t_start = _time.time()

        for try_idx in range(n_tries):
            try:
                model = hmm.GaussianHMM(
                    n_components=N_STATES,
                    covariance_type="full",     # full协方差: 捕捉特征间关联
                    n_iter=200,                 # 降低迭代次数(通常 80-100 轮就收敛)
                    tol=1e-4,
                    init_params="stmc",
                    params="stmc",
                    verbose=False,
                )
                model.fit(X_norm, lengths_total)
                score = model.score(X_norm, lengths_total)
                if score > best_score:
                    best_score = score
                    best_model = model
                    # 保存标准化参数
                    best_model._feature_mean = mean
                    best_model._feature_std = std
                elapsed = _time.time() - t_start
                print(f"      第 {try_idx + 1}/{n_tries} 次训练完成 (耗时 {elapsed:.1f}s, loglik={score:.1f})")
            except Exception as e:
                print(f"      第 {try_idx + 1} 次训练失败: {e}")
                continue

        if best_model is None:
            print("   ❌ 训练失败: 所有初始化都未收敛")
            return False

        total_time = _time.time() - t_start
        print(f"   ⏱️  训练总耗时: {total_time:.1f}s")

        self.gestures[name] = best_model
        return True

    def _train_silence_model(self, samples_list: List[List[Dict]]) -> bool:
        if hmm is None:
            return False

        all_features = []
        lengths = []
        for samples in samples_list:
            features = self._extract_features(samples)
            if len(features) > 10:
                all_features.append(features)
                lengths.append(len(features))

        if len(all_features) < 3:
            return False

        X = np.vstack(all_features)
        lengths = np.array(lengths)

        # 标准化
        mean = X.mean(axis=0)
        std = X.std(axis=0) + 0.001
        X_norm = (X - mean) / std

        try:
            model = hmm.GaussianHMM(
                n_components=2,
                covariance_type="diag",
                n_iter=200,
                tol=1e-4,
            )
            model.fit(X_norm, lengths)
            model._feature_mean = mean
            model._feature_std = std
            self.silence_model = model
            return True
        except Exception as e:
            print(f"   ❌ 静默模型训练失败: {e}")
            return False

    def _model_loglik_per_frame(self, model, features: np.ndarray) -> float:
        """计算模型对特征序列的每帧平均对数似然(长度归一化)。"""
        if len(features) < 3:
            return -float('inf')
        try:
            # 应用该模型训练时的标准化参数
            mean = getattr(model, '_feature_mean', np.zeros(features.shape[1]))
            std = getattr(model, '_feature_std', np.ones(features.shape[1]))
            features_norm = (features - mean) / std
            total_score = model.score(features_norm)
            return total_score / len(features)   # 归一化!
        except Exception:
            return -float('inf')

    # ==================== 识别 (改进版: 长度归一化评分) ====================

    def _recognize(self, samples: List[Dict]) -> Optional[str]:
        if not self.gestures:
            return None

        features = self._extract_features(samples)
        if len(features) < 10:
            return None

        best_name = None
        best_score = -float('inf')

        for name, model in self.gestures.items():
            score = self._model_loglik_per_frame(model, features)
            if score > best_score:
                best_score = score
                best_name = name

        # 阈值过滤
        if best_score < MATCH_THRESHOLD:
            return None

        # 与静默模型比较 (防止静止时误触发)
        if self.silence_model is not None:
            silence_score = self._model_loglik_per_frame(self.silence_model, features)
            if best_score - silence_score < SILENCE_MARGIN:
                return None

        return best_name

    # ==================== 蓝牙 ====================

    async def _connect(self):
        print(f"🔗 连接 {self.mac}...")
        try:
            self.ring = await sdk.connect_ring(
                address=self.mac,
                command_timeout_s=20.0,
                auto_time_sync=True
            )
            self.is_connected = True
            print("✅ 已连接")
        except Exception as e:
            print(f"❌ 连接失败: {e}")
            self.ring = None
            self.is_connected = False

    async def _ensure_connected(self) -> bool:
        if self.is_connected and self.ring:
            return True
        await self._connect()
        return self.is_connected

    async def _wait_for_data(self, timeout_s: float = 1.0):
        if not self.is_connected or self.ring is None:
            return None
        try:
            return await sdk.wait_sensor_data(self.ring, timeout_s=timeout_s)
        except Exception:
            return None

    async def _collect_samples(self, duration: float = SAMPLE_DURATION) -> List[Dict]:
        samples = []
        start = time.time()

        try:
            await sdk.start_sensor_report(self.ring)
            while time.time() - start < duration:
                batch = await self._wait_for_data()
                if batch:
                    for s in batch.samples:
                        samples.append({
                            'accel_x': s.accel_x, 'accel_y': s.accel_y, 'accel_z': s.accel_z,
                            'gyro_x': s.gyro_x, 'gyro_y': s.gyro_y, 'gyro_z': s.gyro_z,
                        })
        except Exception as e:
            print(f"⚠️ 采集异常: {e}")
        finally:
            try:
                await sdk.stop_sensor_report(self.ring)
            except:
                pass

        return samples

    # ==================== 模型持久化 ====================

    def _save_models(self):
        """保存所有训练好的模型到磁盘(含版本标记)。"""
        if not self.gestures and self.silence_model is None:
            return
        data = {
            "version": MODEL_VERSION,
            "gestures": self.gestures,
            "silence_model": self.silence_model,
        }
        try:
            with open(MODEL_FILE, "wb") as f:
                pickle.dump(data, f)
            print(f"💾 模型已保存到 {MODEL_FILE}")
        except Exception as e:
            print(f"⚠️ 保存模型失败: {e}")

    def _load_models(self):
        """从磁盘加载已有的模型(版本不匹配时自动跳过)。"""
        if not MODEL_FILE.exists():
            return
        try:
            with open(MODEL_FILE, "rb") as f:
                data = pickle.load(f)
            version = data.get("version", "unknown")
            if version != MODEL_VERSION:
                print(f"⚠️ 模型文件版本 ({version}) 与当前版本 ({MODEL_VERSION}) 不匹配, 已跳过")
                return
            self.gestures = data.get("gestures", {})
            self.silence_model = data.get("silence_model")
            if self.gestures:
                names = ", ".join(self.gestures.keys())
                print(f"📂 已加载 {len(self.gestures)} 个模型: {names}")
        except Exception as e:
            print(f"⚠️ 加载模型失败: {e}")

    # ==================== 训练流程 (100次, 带进度条 + 提前终止) ====================

    async def train_action(self):
        if not await self._ensure_connected():
            return

        name = input("\n📝 动作名称 (如: 画圆、挥手、前后): ").strip()
        if not name:
            return

        print(f"\n🔔 采集 '{name}'，共 {TRAIN_EPOCHS} 次")
        print("   每次做同样的动作，速度尽量一致")
        print("   提示: 采集过程中按 Ctrl+C 可提前结束(已采集的仍然有效)")
        input("按 Enter 开始...")

        all_samples = []
        try:
            for i in range(1, TRAIN_EPOCHS + 1):
                # 每10次显示一次进度
                if (i - 1) % 10 == 0:
                    print(f"\n   📊 进度: {i - 1}/{TRAIN_EPOCHS}")

                input(f"\n⏳ 第 {i}/{TRAIN_EPOCHS} 次: 按 Enter 后开始做动作")

                print("   ⏰ 3...2...1... 🎬 做动作！")
                samples = await self._collect_samples()

                if len(samples) > 10:
                    all_samples.append(samples)
                    bar = "█" * (i // (TRAIN_EPOCHS // 40) if TRAIN_EPOCHS >= 40 else i * 2)
                    print(f"   ✅ [{bar:<40}] {i}/{TRAIN_EPOCHS}  ({len(samples)} 样本)")
                else:
                    print(f"   ❌ 数据太少, 跳过")
        except KeyboardInterrupt:
            print(f"\n   ⏸️  提前终止于第 {len(all_samples)} 次")

        if len(all_samples) < 5:
            print(f"❌ 有效样本不足 ({len(all_samples)}), 至少需要5次")
            return

        print(f"\n🧠 正在训练 HMM ({len(all_samples)} 个样本)...")
        if self._train_hmm(name, all_samples):
            print(f"✅ '{name}' 训练完成 (共 {len(all_samples)} 次有效采集)")
            self._save_models()
        else:
            print("❌ 训练失败")

    async def train_silence(self):
        print("\n🔔 采集静默数据 (保持手部完全静止)")
        input("按 Enter 开始...")

        all_samples = []

        for i in range(1, 6):
            input(f"\n⏳ 第 {i}/5 次: 按 Enter")
            print("   🧘 保持静止 2 秒")
            samples = await self._collect_samples()
            print(f"   📊 {len(samples)} 个样本")
            if len(samples) > 10:
                all_samples.append(samples)

        if self._train_silence_model(all_samples):
            print("✅ 静默模型训练完成")
            self._save_models()

    # ==================== 识别流程 (改进: 重叠窗口 + 更多IMU数据) ====================

    async def recognize(self):
        if not await self._ensure_connected():
            return

        if not self.gestures:
            print("❌ 没有训练的动作")
            return

        print("\n🔍 开始识别")
        print(f"  已训练: {', '.join(self.gestures.keys())}")
        print("  做动作即可识别")
        print("  按 Ctrl+C 退出\n")

        window = deque(maxlen=WINDOW_SIZE)
        cooldown = 0

        try:
            await sdk.start_sensor_report(self.ring)
            print("✅ IMU已开启")

            while True:
                batch = await self._wait_for_data()
                if batch is None:
                    continue

                for s in batch.samples:
                    window.append({
                        'accel_x': s.accel_x, 'accel_y': s.accel_y, 'accel_z': s.accel_z,
                        'gyro_x': s.gyro_x, 'gyro_y': s.gyro_y, 'gyro_z': s.gyro_z,
                    })

                    if cooldown > 0:
                        cooldown -= 1
                        continue

                    # 重叠窗口: 每5个样本检测一次, 而非等满30个(更快响应)
                    if len(window) >= 20 and len(window) % 5 == 0:
                        current = list(window)
                        mags = [np.sqrt(w['accel_x'] ** 2 + w['accel_y'] ** 2 + w['accel_z'] ** 2)
                                for w in current]
                        diff = max(mags) - min(mags)

                        if diff > 600:
                            match = self._recognize(current)
                            if match:
                                print(f"🎯 {match}")
                                cooldown = 15
                                window.clear()

        except KeyboardInterrupt:
            print("\n👋 退出")
        finally:
            try:
                await sdk.stop_sensor_report(self.ring)
            except:
                pass


# ==================== 主程序 ====================

async def main():
    trainer = GestureTrainer()

    print(f"""
╔══════════════════════════════════════╗
║   🪄 手势识别 v{MODEL_VERSION}                  ║
║   14维特征 + HMM全协方差 + 100次训练  ║
╚══════════════════════════════════════╝
    """)

    # 自动连接戒指
    await trainer._connect()

    while True:
        print("\n" + "─" * 40)
        print("1. 训练动作")
        print("2. 训练静默基线 (减少误识别)")
        print("3. 识别模式")
        print("4. 清空所有训练")
        print("5. 退出")

        choice = input("\n选择: ").strip()

        if choice == "1":
            await trainer.train_action()
        elif choice == "2":
            await trainer.train_silence()
        elif choice == "3":
            await trainer.recognize()
        elif choice == "4":
            trainer.gestures = {}
            trainer.silence_model = None
            if MODEL_FILE.exists():
                MODEL_FILE.unlink()
            print("✅ 已清空所有训练数据和模型文件")
        elif choice == "5":
            break


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 再见")