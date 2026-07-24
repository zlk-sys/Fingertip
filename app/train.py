#!/usr/bin/env python3
"""
戒指手势识别 v3.2 - 使用方向特征区分不同动作
"""

import asyncio
import json
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
MAC_ADDRESS = "E3:07:8F:FE:F9:02"
SAMPLE_DURATION = 2.0
TRAIN_EPOCHS = 8
WINDOW_SIZE = 30
N_STATES = 5
MATCH_THRESHOLD = -300


class GestureTrainer:
    def __init__(self):
        self.gestures: Dict[str, hmm.GaussianHMM] = {}
        self.silence_model: Optional[hmm.GaussianHMM] = None
        self.ring = None
        self.is_connected = False
        self.mac = MAC_ADDRESS
        self._connect()

    # ==================== 核心：特征提取 (区分度更高) ====================

    def _extract_features(self, samples: List[Dict]) -> np.ndarray:
        """
        提取高区分度特征
        关键：使用方向特征，而不是只有大小
        """
        if not samples:
            return np.array([])

        # 原始数据
        ax = np.array([s['accel_x'] for s in samples]) / 2048.0
        ay = np.array([s['accel_y'] for s in samples]) / 2048.0
        az = np.array([s['accel_z'] for s in samples]) / 2048.0
        gx = np.array([s['gyro_x'] for s in samples]) / 2000.0
        gy = np.array([s['gyro_y'] for s in samples]) / 2000.0
        gz = np.array([s['gyro_z'] for s in samples]) / 2000.0

        # 合加速度
        mag = np.sqrt(ax ** 2 + ay ** 2 + az ** 2)

        # 归一化方向向量 (关键：这保留了方向信息)
        mag_safe = mag + 0.001
        ax_norm = ax / mag_safe
        ay_norm = ay / mag_safe
        az_norm = az / mag_safe

        # 方向变化率 (画圆时持续变化，直线时变化小)
        dx = np.gradient(ax_norm)
        dy = np.gradient(ay_norm)
        dz = np.gradient(az_norm)
        direction_change = np.sqrt(dx ** 2 + dy ** 2 + dz ** 2)

        # 角度变化 (进一步区分)
        # 计算相邻帧之间的角度
        angle_change = np.zeros(len(ax_norm))
        for i in range(1, len(ax_norm)):
            dot = ax_norm[i] * ax_norm[i - 1] + ay_norm[i] * ay_norm[i - 1] + az_norm[i] * az_norm[i - 1]
            dot = np.clip(dot, -1, 1)
            angle_change[i] = np.arccos(dot)

        # 特征矩阵: 7维
        # [合加速度, 方向X, 方向Y, 方向变化率, 角度变化, 陀螺仪X, 陀螺仪Y]
        features = np.column_stack([
            mag,
            ax_norm,
            ay_norm,
            direction_change,
            angle_change,
            gx,
            gy
        ])

        # 去掉NaN
        features = np.nan_to_num(features, nan=0.0)

        return features

    # ==================== 训练 ====================

    def _train_hmm(self, name: str, samples_list: List[List[Dict]]) -> bool:
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

        try:
            model = hmm.GaussianHMM(
                n_components=N_STATES,
                covariance_type="diag",
                n_iter=300,
                tol=0.001,
                init_params="stmc",
                params="stmc"
            )
            model.fit(X, lengths)
            self.gestures[name] = model
            return True
        except Exception as e:
            print(f"   ❌ 训练失败: {e}")
            return False

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

        try:
            model = hmm.GaussianHMM(
                n_components=2,
                covariance_type="diag",
                n_iter=100
            )
            model.fit(X, lengths)
            self.silence_model = model
            return True
        except Exception as e:
            print(f"   ❌ 静默模型训练失败: {e}")
            return False

    # ==================== 识别 ====================

    def _recognize(self, samples: List[Dict]) -> Optional[str]:
        if not self.gestures:
            return None

        features = self._extract_features(samples)
        if len(features) < 10:
            return None

        best_name = None
        best_score = -float('inf')

        for name, model in self.gestures.items():
            try:
                score = model.score(features)
                if score > best_score:
                    best_score = score
                    best_name = name
            except Exception:
                continue

        if best_score < MATCH_THRESHOLD:
            return None

        # 与静默模型比较
        if self.silence_model is not None:
            try:
                silence_score = self.silence_model.score(features)
                if best_score - silence_score < 50:
                    return None
            except Exception:
                pass

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

    # ==================== 训练流程 ====================

    async def train_action(self):
        if not await self._ensure_connected():
            return

        name = input("\n📝 动作名称 (如: 画圆、挥手、前后): ").strip()
        if not name:
            return

        print(f"\n🔔 采集 '{name}'，共 {TRAIN_EPOCHS} 次")
        print("   每次做同样的动作，速度尽量一致")
        input("按 Enter 开始...")

        all_samples = []

        for i in range(1, TRAIN_EPOCHS + 1):
            input(f"\n⏳ 第 {i}/{TRAIN_EPOCHS} 次: 按 Enter 后开始做动作")

            print("   ⏰ 3...2...1... 🎬 做动作！")
            samples = await self._collect_samples()
            print(f"   📊 {len(samples)} 个样本")

            if len(samples) > 10:
                all_samples.append(samples)
            else:
                print("   ❌ 数据太少，重试")
                i -= 1

        if len(all_samples) < 3:
            print("❌ 样本不足")
            return

        if self._train_hmm(name, all_samples):
            print(f"✅ '{name}' 训练完成")
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

    # ==================== 识别流程 ====================

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

                    if len(window) == WINDOW_SIZE:
                        # 检测是否有动作
                        mags = [np.sqrt(w['accel_x'] ** 2 + w['accel_y'] ** 2 + w['accel_z'] ** 2) for w in window]
                        diff = max(mags) - min(mags)

                        if diff > 600:
                            match = self._recognize(list(window))
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

    print("""
╔══════════════════════════════════════╗
║   🪄 手势识别 v3.2                  ║
║   使用方向特征区分不同动作           ║
╚══════════════════════════════════════╝
    """)

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
            print("✅ 已清空")
        elif choice == "5":
            break


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 再见")