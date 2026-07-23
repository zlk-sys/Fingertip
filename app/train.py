#!/usr/bin/env python3
"""
Ring Sound Gesture Trainer & Recognizer v2.1

用法:
  python gesture_trainer.py

录入模式:
  1. 输入动作名称
  2. 按回车开始采集 (采集2秒数据)
  3. 重复5次
  4. 自动生成数据模型

识别模式:
  实时分析IMU数据，匹配已训练的动作
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import numpy as np
from collections import deque

import sdk.ring_sound as sdk

# 存储文件
DATA_FILE = Path("gesture_training.json")
CONFIG_FILE = Path("ring_config.json")

# 采集参数
SAMPLE_DURATION = 2.0
SAMPLE_RATE = 25
WINDOW_SIZE = 10
MAX_RECONNECT_ATTEMPTS = 3


class GestureTrainer:
    def __init__(self):
        self.trained_gestures: Dict[str, Dict] = {}
        self.ring: Optional[sdk.RingSoundClient] = None
        self.is_connected = False
        self.mac_address: Optional[str] = None
        self._load_config()
        self._load_data()
        self._is_gesture_mode = False

    # ==================== 配置管理 ====================

    def _load_config(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
                    self.mac_address = config.get("mac_address")
                    if self.mac_address:
                        print(f"📋 读取到已保存设备: {self.mac_address}")
            except Exception as e:
                print(f"⚠️ 读取配置失败: {e}")
                self.mac_address = None
        else:
            self.mac_address = None

    def _save_config(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump({"mac_address": self.mac_address}, f, indent=2)
        print(f"💾 已保存设备地址到 {CONFIG_FILE}")

    # ==================== 数据管理 ====================

    def _load_data(self):
        if DATA_FILE.exists():
            try:
                with open(DATA_FILE, "r") as f:
                    self.trained_gestures = json.load(f)
                if self.trained_gestures:
                    print(f"✅ 加载了 {len(self.trained_gestures)} 个已训练动作")
            except Exception as e:
                print(f"⚠️ 加载数据失败: {e}")
                self.trained_gestures = {}
        else:
            self.trained_gestures = {}

    def _save_data(self):
        with open(DATA_FILE, "w") as f:
            json.dump(self.trained_gestures, f, indent=2, ensure_ascii=False)
        print(f"💾 已保存到 {DATA_FILE}")

    # ==================== 蓝牙扫描 ====================

    async def scan_and_select(self) -> bool:
        print("\n🔍 正在扫描蓝牙设备...")
        print("   请确保戒指已开机并处于广播状态\n")

        try:
            devices = await sdk.scan_rings(timeout_s=8.0)
        except Exception as e:
            print(f"❌ 扫描失败: {e}")
            return False

        if not devices:
            print("❌ 未找到任何蓝牙设备")
            return False

        ring_devices = []
        for d in devices:
            name = d.name or ""
            if "ring" in name.lower() or "ringsound" in name.lower() or d.address.startswith("F1:C1"):
                ring_devices.append(d)

        if not ring_devices:
            print("❌ 未找到戒指设备")
            return False

        print(f"\n📋 找到 {len(ring_devices)} 个戒指设备:")
        for i, dev in enumerate(ring_devices, 1):
            name = dev.name or "未知设备"
            rssi = f"信号: {dev.rssi}dBm" if dev.rssi else ""
            print(f"  {i}. {name} - {dev.address} {rssi}")

        while True:
            try:
                choice = input(f"\n请选择设备 (1-{len(ring_devices)}): ").strip()
                idx = int(choice) - 1
                if 0 <= idx < len(ring_devices):
                    selected = ring_devices[idx]
                    self.mac_address = selected.address
                    self._save_config()
                    print(f"✅ 已选择: {selected.name or '未知设备'} - {self.mac_address}")
                    return True
                else:
                    print(f"❌ 请输入 1-{len(ring_devices)}")
            except ValueError:
                print("❌ 请输入数字")

    # ==================== 连接管理（含自动重连） ====================

    async def connect(self, max_retries: int = 5) -> bool:
        if self.is_connected and self.ring is not None:
            # 验证连接是否真的有效
            try:
                # 尝试发送一个简单命令验证连接
                await sdk.get_system_info(self.ring, timeout_s=3.0)
                return True
            except Exception:
                print("⚠️ 连接已失效，重新连接...")
                self.is_connected = False
                self.ring = None

        if not self.mac_address:
            print("📋 未找到已保存的设备")
            if not await self.scan_and_select():
                return False

        for attempt in range(max_retries):
            if attempt > 0:
                wait_time = min(2.0 * attempt, 8.0)
                print(f"\n⏳ 等待 {wait_time:.0f}秒后重试 ({attempt+1}/{max_retries})...")
                await asyncio.sleep(wait_time)

            print(f"🔗 正在连接戒指 {self.mac_address} (尝试 {attempt+1}/{max_retries})...")
            try:
                self.ring = await sdk.connect_ring(
                    address=self.mac_address,
                    command_timeout_s=30.0,
                    auto_time_sync=True
                )
                self.is_connected = True
                self._is_gesture_mode = False
                print("✅ 连接成功！")
                return True
            except Exception as e:
                print(f"❌ 连接失败: {e}")
                if self.ring is not None:
                    try:
                        await self.ring.disconnect()
                    except:
                        pass
                    self.ring = None
                self.is_connected = False

        print(f"\n❌ 连续 {max_retries} 次重试失败")
        return False

    async def _ensure_connected(self) -> bool:
        """确保连接有效，如果断开则自动重连"""
        if self.is_connected and self.ring is not None:
            return True

        print("\n⚠️ 连接已断开，尝试重新连接...")
        return await self.connect(max_retries=3)

    async def disconnect(self):
        if self.ring is not None:
            try:
                await self.ring.disconnect()
            except Exception as e:
                print(f"⚠️ 断开时出错: {e}")
            self.ring = None
            self.is_connected = False
            self._is_gesture_mode = False
            print("🔌 已断开连接")
        else:
            print("ℹ️  当前未连接")

    # ==================== 带重试的IMU操作 ====================

    async def _start_sensor_report_with_retry(self) -> bool:
        """带重试的start_sensor_report"""
        for attempt in range(3):
            try:
                if not await self._ensure_connected():
                    return False
                start_info = await sdk.start_sensor_report(self.ring, timeout_s=5.0)
                print(f"   ✅ IMU已开启 (采样率: {start_info.sample_rate_hz}Hz)")
                return True
            except sdk.DeviceError as e:
                if e.error_code == 2:  # 录音模式
                    print("   ℹ️ 当前在录音模式，请手动单击戒指切换")
                    return False
                print(f"   ⚠️ 设备错误 (尝试 {attempt+1}/3): {e}")
            except sdk.TransportError as e:
                print(f"   ⚠️ 传输错误 (尝试 {attempt+1}/3): {e}")
                self.is_connected = False
                await asyncio.sleep(1)
            except Exception as e:
                print(f"   ⚠️ 错误 (尝试 {attempt+1}/3): {e}")
                await asyncio.sleep(1)

        return False

    async def _stop_sensor_report_safe(self):
        """安全停止IMU上报"""
        try:
            if self.ring is not None and self.is_connected:
                await sdk.stop_sensor_report(self.ring)
        except Exception:
            pass  # 忽略停止时的错误

    async def _wait_sensor_data_safe(self, timeout_s: float = 1.0):
        """安全等待IMU数据，断连时自动重连"""
        for attempt in range(3):
            try:
                if not await self._ensure_connected():
                    await asyncio.sleep(0.5)
                    continue
                return await sdk.wait_sensor_data(self.ring, timeout_s=timeout_s)
            except sdk.TransportError:
                print("   ⚠️ 传输断开，尝试重连...")
                self.is_connected = False
                await asyncio.sleep(0.5)
                continue
            except sdk.TimeoutError:
                return None  # 超时不算错误
            except Exception as e:
                print(f"   ⚠️ 等待数据异常: {e}")
                await asyncio.sleep(0.5)
                continue
        return None

    # ==================== 手势模式检测 ====================

    async def _ensure_gesture_mode(self) -> bool:
        """确保戒指处于手势模式"""
        if not await self._ensure_connected():
            return False

        if self._is_gesture_mode:
            print("   ✅ 已处于手势模式")
            return True

        print("   🔍 检测当前模式...")

        try:
            start_info = await sdk.start_sensor_report(self.ring, timeout_s=5.0)
            print(f"   ✅ 已处于手势模式 (采样率: {start_info.sample_rate_hz}Hz)")
            await self._stop_sensor_report_safe()
            self._is_gesture_mode = True
            return True
        except sdk.DeviceError as e:
            if e.error_code == 2:
                print("   ℹ️  当前处于录音模式")
            else:
                print(f"   ⚠️ 设备错误: {e}")
                return False
        except sdk.TransportError:
            print("   ⚠️ 连接断开，重连后重试...")
            self.is_connected = False
            if await self._ensure_connected():
                return await self._ensure_gesture_mode()
            return False
        except Exception as e:
            print(f"   ⚠️ 检测失败: {e}")
            return False

        print("\n   👆 请手动单击戒指按钮切换到手势模式")
        print("      然后按 Enter 继续...")
        input()

        try:
            start_info = await sdk.start_sensor_report(self.ring, timeout_s=5.0)
            print(f"   ✅ 已切换到手势模式")
            await self._stop_sensor_report_safe()
            self._is_gesture_mode = True
            return True
        except Exception as e:
            print(f"   ❌ 切换失败: {e}")
            return False

    # ==================== 数据采集 ====================

    async def _collect_samples(self, duration: float = SAMPLE_DURATION) -> List[Dict]:
        """采集指定时长的IMU数据，断连自动恢复"""
        samples = []
        start_time = time.time()

        if not await self._start_sensor_report_with_retry():
            return samples

        try:
            while time.time() - start_time < duration:
                batch = await self._wait_sensor_data_safe(timeout_s=0.5)
                if batch is None:
                    continue
                for sample in batch.samples:
                    samples.append({
                        'timestamp': sample.timestamp_ms,
                        'accel_x': sample.accel_x,
                        'accel_y': sample.accel_y,
                        'accel_z': sample.accel_z,
                        'gyro_x': sample.gyro_x,
                        'gyro_y': sample.gyro_y,
                        'gyro_z': sample.gyro_z,
                    })
        finally:
            await self._stop_sensor_report_safe()

        return samples

    def _extract_features(self, samples: List[Dict]) -> Dict:
        if not samples:
            return {}

        accel_x = np.array([s['accel_x'] for s in samples])
        accel_y = np.array([s['accel_y'] for s in samples])
        accel_z = np.array([s['accel_z'] for s in samples])
        gyro_x = np.array([s['gyro_x'] for s in samples])
        gyro_y = np.array([s['gyro_y'] for s in samples])
        gyro_z = np.array([s['gyro_z'] for s in samples])

        accel_mag = np.sqrt(accel_x**2 + accel_y**2 + accel_z**2)

        return {
            'accel_mean': float(np.mean(accel_mag)),
            'accel_std': float(np.std(accel_mag)),
            'accel_range': float(np.max(accel_mag) - np.min(accel_mag)),
            'accel_x_mean': float(np.mean(accel_x)),
            'accel_y_mean': float(np.mean(accel_y)),
            'accel_z_mean': float(np.mean(accel_z)),
            'gyro_mag_mean': float(np.mean(np.sqrt(gyro_x**2 + gyro_y**2 + gyro_z**2))),
            'gyro_x_mean': float(np.mean(gyro_x)),
            'gyro_y_mean': float(np.mean(gyro_y)),
            'gyro_z_mean': float(np.mean(gyro_z)),
            'peak_count': self._count_peaks(accel_mag),
            'sample_count': len(samples)
        }

    def _count_peaks(self, data: np.ndarray, threshold: float = 500) -> int:
        if len(data) < 3:
            return 0
        peaks = 0
        mean_val = np.mean(data)
        std_val = np.std(data)
        if std_val < 10:
            return 0
        for i in range(1, len(data) - 1):
            if data[i] > data[i-1] and data[i] > data[i+1]:
                if data[i] > mean_val + threshold:
                    peaks += 1
        return peaks

    def _similarity(self, f1: Dict, f2: Dict) -> float:
        keys = ['accel_range', 'accel_std', 'gyro_mag_mean', 'peak_count']

        total_diff = 0
        for key in keys:
            if key in f1 and key in f2:
                v1 = f1[key]
                v2 = f2[key]
                if v1 == 0 and v2 == 0:
                    diff = 0
                elif v1 == 0:
                    diff = abs(v2) / 100
                elif v2 == 0:
                    diff = abs(v1) / 100
                else:
                    diff = abs(v1 - v2) / (abs(v1) + abs(v2))
                total_diff += diff

        if 'accel_x_mean' in f1 and 'accel_x_mean' in f2:
            ax_diff = abs(f1['accel_x_mean'] - f2['accel_x_mean']) / 2048
            ay_diff = abs(f1['accel_y_mean'] - f2['accel_y_mean']) / 2048
            az_diff = abs(f1['accel_z_mean'] - f2['accel_z_mean']) / 2048
            total_diff += (ax_diff + ay_diff + az_diff) / 3

        return total_diff

    # ==================== 训练 ====================

    async def train_action(self):
        if not await self._ensure_connected():
            return

        print("\n🔔 准备训练...")
        if not await self._ensure_gesture_mode():
            print("❌ 无法进入手势模式")
            return

        name = input("\n📝 输入动作名称: ").strip()
        if not name:
            print("❌ 动作名称不能为空")
            return

        if name in self.trained_gestures:
            print(f"⚠️ 动作 '{name}' 已存在，将覆盖")
            confirm = input("确认覆盖? (y/n): ").strip().lower()
            if confirm != 'y':
                return

        print(f"\n🔔 准备采集 '{name}'")
        print(f"   每次采集 {SAMPLE_DURATION} 秒数据，共5次")
        print("   每次按 Enter 开始采集")
        print("   请保持手部静止，等待提示再开始动作")
        input("\n按 Enter 开始...")

        all_features = []

        for i in range(1, 6):
            input(f"\n⏳ 第 {i}/5 次: 按 Enter 开始采集...")

            print("   ⏰ 准备... 3")
            await asyncio.sleep(0.5)
            print("   ⏰ 2")
            await asyncio.sleep(0.5)
            print("   ⏰ 1")
            await asyncio.sleep(0.5)
            print("   🎬 开始做动作！")

            # 确保连接有效
            if not await self._ensure_connected():
                print("   ❌ 连接断开，请重试")
                i -= 1
                continue

            samples = await self._collect_samples(duration=SAMPLE_DURATION)
            print(f"   📊 采集到 {len(samples)} 个样本")

            if len(samples) < 10:
                print("   ❌ 数据太少，请重试")
                i -= 1
                continue

            features = self._extract_features(samples)
            all_features.append(features)
            print(f"   ✅ 第 {i} 次采集完成")

        if len(all_features) < 5:
            print("\n❌ 有效采集次数不足，训练失败")
            return

        avg_features = {}
        key_list = ['accel_mean', 'accel_std', 'accel_range', 'accel_x_mean', 'accel_y_mean', 'accel_z_mean',
                   'gyro_mag_mean', 'gyro_x_mean', 'gyro_y_mean', 'gyro_z_mean', 'peak_count']

        for key in key_list:
            values = [f.get(key, 0) for f in all_features]
            avg_features[key] = sum(values) / len(values)

        self.trained_gestures[name] = {
            'template': avg_features,
            'samples_count': len(all_features) * 50,
            'features': all_features
        }
        self._save_data()

        print(f"\n✅ 动作 '{name}' 训练完成！")
        print(f"   📊 成功采集: {len(all_features)}/5 次")

    # ==================== 识别 ====================

    async def recognize(self):
        if not await self._ensure_connected():
            return

        if not self.trained_gestures:
            print("\n❌ 没有已训练的动作，请先训练")
            return

        print("\n🔔 准备识别...")
        if not await self._ensure_gesture_mode():
            print("❌ 无法进入手势模式")
            return

        print("\n🔍 开始识别模式 (持续监听)")
        print(f"  已训练动作: {', '.join(self.trained_gestures.keys())}")
        print("  直接做动作即可，无需按键")
        print("  按 Ctrl+C 退出\n")

        window = deque(maxlen=25)

        if not await self._start_sensor_report_with_retry():
            print("   ❌ 无法开启IMU")
            return

        print("   ✅ IMU已开启\n")

        try:
            while True:
                batch = await self._wait_sensor_data_safe(timeout_s=1.0)
                if batch is None:
                    # 检查连接是否还在
                    if not await self._ensure_connected():
                        print("   ⚠️ 连接断开，尝试恢复...")
                        await asyncio.sleep(1)
                        # 重新开启IMU
                        if not await self._start_sensor_report_with_retry():
                            print("   ❌ 无法恢复IMU")
                            break
                    continue

                for sample in batch.samples:
                    window.append({
                        'accel_x': sample.accel_x,
                        'accel_y': sample.accel_y,
                        'accel_z': sample.accel_z,
                        'gyro_x': sample.gyro_x,
                        'gyro_y': sample.gyro_y,
                        'gyro_z': sample.gyro_z,
                    })

                    if len(window) == window.maxlen:
                        accel_mags = [np.sqrt(s['accel_x']**2 + s['accel_y']**2 + s['accel_z']**2) for s in window]
                        diff = max(accel_mags) - min(accel_mags)

                        if diff > 800:
                            features = self._extract_features(list(window))
                            match = self._match_action(features)
                            if match:
                                print(f"🎯 识别到: {match}")
                            window.clear()

        except KeyboardInterrupt:
            print("\n\n👋 退出识别模式")
        finally:
            await self._stop_sensor_report_safe()
            print("   IMU已关闭")

    def _match_action(self, features: Dict) -> Optional[str]:
        if not features:
            return None

        best_match = None
        best_score = float('inf')
        threshold = 2.0

        for name, data in self.trained_gestures.items():
            template = data.get('template', {})
            if not template:
                continue
            score = self._similarity(features, template)
            if score < best_score:
                best_score = score
                best_match = name

        if best_score < threshold and best_match:
            return best_match
        return None

    def list_actions(self):
        if not self.trained_gestures:
            print("\n📭 还没有训练任何动作")
            return

        print("\n📋 已训练的动作:")
        for i, (name, data) in enumerate(self.trained_gestures.items(), 1):
            sample_count = data.get('samples_count', 0)
            print(f"  {i}. {name} (样本数: {sample_count})")

    def delete_action(self):
        if not self.trained_gestures:
            print("\n📭 没有动作可删除")
            return

        self.list_actions()
        name = input("\n📝 输入要删除的动作名称: ").strip()
        if name in self.trained_gestures:
            del self.trained_gestures[name]
            self._save_data()
            print(f"✅ 已删除 '{name}'")
        else:
            print(f"❌ 未找到 '{name}'")

    def show_status(self):
        print("\n📊 当前状态:")
        print(f"  设备状态: {'✅ 已连接' if self.is_connected else '❌ 未连接'}")
        if self.mac_address:
            print(f"  设备地址: {self.mac_address}")
        print(f"  已训练动作: {len(self.trained_gestures)} 个")
        if self.trained_gestures:
            names = ", ".join(self.trained_gestures.keys())
            print(f"  动作列表: {names}")


# ==================== 主菜单 ====================

async def main():
    trainer = GestureTrainer()

    if trainer.mac_address:
        print("\n🔄 尝试自动连接...")
        await trainer.connect(max_retries=5)

    print("""
╔══════════════════════════════════════╗
║   🪄 戒指手势训练器 v2.1            ║
║   基于IMU原始数据的动作训练与识别    ║
╚══════════════════════════════════════╝
    """)

    while True:
        print("\n" + "─" * 50)
        trainer.show_status()
        print("\n📋 主菜单")
        print("  1. 🔗 连接/切换设备")
        print("  2. 🎯 训练新动作")
        print("  3. 🔍 识别动作 (持续监听)")
        print("  4. 📋 查看已训练动作")
        print("  5. 🗑️  删除动作")
        print("  6. 🔌 断开设备")
        print("  7. 🚪 退出")
        print("─" * 50)

        choice = input("请选择 (1-7): ").strip()

        if choice == "1":
            if trainer.is_connected:
                await trainer.disconnect()
            await trainer.scan_and_select()
            await trainer.connect()
        elif choice == "2":
            await trainer.train_action()
        elif choice == "3":
            await trainer.recognize()
        elif choice == "4":
            trainer.list_actions()
        elif choice == "5":
            trainer.delete_action()
        elif choice == "6":
            await trainer.disconnect()
        elif choice == "7":
            await trainer.disconnect()
            print("\n👋 再见！")
            break
        else:
            print("❌ 无效选项，请重新选择")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 已退出")
    except Exception as e:
        print(f"\n❌ 程序异常: {e}")
        import traceback
        traceback.print_exc()
        input("按 Enter 退出...")