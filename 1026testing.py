# xm125_test.py
# Minimal A121 example for Raspberry Pi (no server)
# Based on Acconeer AB example, adapted for direct serial connection

from acconeer.exptool import a121


from __future__ import annotations
import npyfile
import numpy as np
import time
import csv
import acconeer.exptool as et
from acconeer.exptool.a121 import Profile
from acconeer.exptool.a121.algo.breathing import RefApp
from acconeer.exptool.a121.algo.breathing._ref_app import (
    BreathingProcessorConfig,
    RefAppConfig,
    get_sensor_config,
)
from acconeer.exptool.a121.algo.presence import ProcessorConfig as PresenceProcessorConfig



# --- 1️⃣ 打开连接 ---
#   如果 XM125 刷的是 "exploration server" 固件，
#   可以直接通过 serial_port=/dev/ttyUSB0 连接。
client = a121.Client.open(
    serial_port="/dev/ttyUSB0",
    # usb_device=True,
    # baudrate=115200,
    # protocol="exploration",
    # flow_control=False,
)

print("✅ Connected to XM125")
print("Server Info:")
print(client.server_info)


# --- 2️⃣ 配置传感器 ---
sensor_config = a121.SensorConfig()
sensor_config.num_points = 6           # 测量点数量
sensor_config.sweeps_per_frame = 4     # 每帧的扫描数
sensor_config.hwaas = 16               # 硬件平均次数（越高越平滑）
client.setup_session(sensor_config)

print("✅ Session setup done")


# --- 3️⃣ 开始采样 ---
client.start_session()

N = 5  # 采 5 帧
for i in range(N):
    result = client.get_next()
    print(f"\nResult {i + 1}:")
    print(result)

client.stop_session()
client.close()

print("\n✅ Done. Connection closed.")
