# xm125_breathing_refapp_pi_v1.py
# XM125 breathing RefApp test on Raspberry Pi -- feasibility CSV version
from __future__ import annotations
import time
from pathlib import Path
import datetime
import csv

import numpy as np
import acconeer.exptool as et
from acconeer.exptool import a121
from acconeer.exptool.a121 import Profile
from acconeer.exptool.a121.algo.breathing import RefApp
from acconeer.exptool.a121.algo.breathing._ref_app import (
    BreathingProcessorConfig,
    RefAppConfig,
    get_sensor_config,
)
from acconeer.exptool.a121.algo.presence import ProcessorConfig as PresenceProcessorConfig


# 雷达认为“人进入”的距离范围（可以按实验改）
ENTER_DISTANCE_MIN = 0.4
ENTER_DISTANCE_MAX = 0.7


def main():
    # 增强版 argument parser：在官方 ExampleArgumentParser 上加一个 prefix
    parser = a121.ExampleArgumentParser()
    parser.add_argument(
        "--prefix",
        type=str,
        default=None,
        help="Output filename prefix (without extension).",
    )
    args = parser.parse_args()
    et.utils.config_logging(args)

    # ---------- 0) 读 session_start_unix ----------
    session_start_path = Path("session_start_unix.txt")
    if session_start_path.exists():
        session_start_unix = float(session_start_path.read_text().strip())
        print(f"Using session_start_unix from file: {session_start_unix}")
    else:
        # 如果单独跑雷达脚本，没有这个文件，就退回到当前时间
        session_start_unix = time.time()
        print(f"No session_start_unix.txt, fallback to {session_start_unix}")

    sensor_id = 1  # XM125 默认就是 1

    # ---------- 1) Breathing processor config ----------
    breathing_processor_config = BreathingProcessorConfig(
        lowest_breathing_rate=8,      # 6 bpm (~10 秒一次呼吸)
        highest_breathing_rate=30,    # 30 bpm (~2 秒一次呼吸)
        time_series_length_s=15,      # 和 cold start 直接相关，可后面对比实验
    )

    # ---------- 2) Presence processor config ----------
    presence_config = PresenceProcessorConfig(
        intra_detection_threshold=4,
        intra_frame_time_const=0.15,
        inter_frame_fast_cutoff=20,
        inter_frame_slow_cutoff=0.2,
        inter_frame_deviation_time_const=0.5,
    )

    # ---------- 3) RefApp (整体应用层) config ----------
    ref_app_config = RefAppConfig(
        use_presence_processor=True,       # 先保持开着，有问题再关
        start_m=0.4,                       # 人大概 0.4–0.7 m
        end_m=0.7,
        num_distances_to_analyze=3,
        distance_determination_duration=5, # 用 5s 决定最佳距离 bin
        breathing_config=breathing_processor_config,
        presence_config=presence_config,
        profile=Profile.PROFILE_5,         # 高频分辨率更高，适合近场小运动
        sweeps_per_frame=16,               # 一帧里做 16 次 sweep（可之后再调）
    )

    # ---------- 4) 生成 sensor_config 并连上 XM125 ----------
    sensor_config = get_sensor_config(ref_app_config=ref_app_config)

    serial_port = "/dev/ttyUSB0"
    client = a121.Client.open(
        serial_port=serial_port,
        override_baudrate=115200,   # 稳定优先
    )
    print("✅ Connected to XM125")
    print("Server Info:")
    print(client.server_info)

    client.setup_session(sensor_config)
    print("✅ Session setup done")

    # ---------- 5) 录原始数据（h5）+ RefApp ----------
    # 文件名前缀：如果有 --prefix，用它；否则自己造一个
    if args.prefix is not None:
        filename_prefix = f"{args.prefix}_radar"
    else:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename_prefix = f"xm125_session_{ts}_radar"

    h5file = f"{filename_prefix}.h5"
    csv_file = f"{filename_prefix}.csv"

    print(f"📄 Radar H5 will be saved to: {h5file}")
    print(f"📄 Radar CSV will be saved to: {csv_file}")

    ratio = 1.0  # 如果后面想整体 scale BPM，可以改这里

    with a121.H5Recorder(h5file, client):
        ref_app = RefApp(client=client, sensor_id=sensor_id, ref_app_config=ref_app_config)
        ref_app.start()

        interrupt_handler = et.utils.ExampleInterruptHandler()
        print("Press Ctrl-C to end session")

        # ⭐ 雷达自动检测的“进入时间”，初始为 None
        radar_enter_time = None

        with open(csv_file, "w", newline="") as csvfile:
            csv_writer = csv.writer(csvfile)
            # 列：专注于 feasibility + radar enter 时间
            csv_writer.writerow([
                "Timestamp",              # 相对时间（相对于 session_start_unix）
                "Unix_Time",              # 绝对 unix time
                "Quality_Flag",           # "breathing", "breathing_no_rate", "presence_only", "none"
                "Breath_Rate_BPM",
                "App_State",
                "Distances_Being_Analyzed",
                "Presence_Detected",
                "Presence_Distance_m",
                "Intra_Presence_Score",
                "Inter_Presence_Score",
                "Presence_Distance_Index",
                "Radar_Enter_Time",       # 雷达第一次检测到 presence in range 的时间（秒），未检测则为空
            ])

            while not interrupt_handler.got_signal:
                processed_data = ref_app.get_next()
                unix_time = time.time()                        # 绝对时间
                current_time = unix_time - session_start_unix  # 从 session_start 算起的相对秒

                try:
                    breathing_res = processed_data.breathing_result
                    presence_res = processed_data.presence_result

                    # 默认值
                    quality_flag = "none"
                    breath_rate_bpm = ""

                    presence_detected = ""
                    presence_distance = ""
                    intra_presence_score = ""
                    inter_presence_score = ""
                    presence_distance_index = ""

                    # ----- 取 presence 相关的 scalar -----
                    if presence_res is not None:
                        presence_detected = presence_res.presence_detected
                        presence_distance = presence_res.presence_distance
                        intra_presence_score = presence_res.intra_presence_score
                        inter_presence_score = presence_res.inter_presence_score

                        if hasattr(presence_res, "extra_result") and presence_res.extra_result is not None:
                            presence_distance_index = presence_res.extra_result.presence_distance_index

                        # ⭐ 如果还没记录过 radar_enter_time，且 presence 距离落在目标范围内，则记录
                        if (
                            radar_enter_time is None
                            and presence_detected
                            and presence_distance is not None
                            and ENTER_DISTANCE_MIN <= presence_distance <= ENTER_DISTANCE_MAX
                        ):
                            radar_enter_time = current_time
                            print(f"📌 Radar enter time marked at {radar_enter_time:.2f} s")

                    # ----- 处理 breathing 相关 -----
                    if breathing_res is not None:
                        br = breathing_res.breathing_rate
                        if br:
                            # case 1: 有 breathing_result 且有 breathing_rate
                            quality_flag = "breathing"
                            breath_rate_bpm = br * ratio
                            print(f"{current_time:.2f}s\t{breath_rate_bpm:.2f} bpm")
                        else:
                            # case 2: 有 breathing_result 但暂时还没出 rate
                            quality_flag = "breathing_no_rate"
                            print(f"{current_time:.2f}s\tCalculating respiration rate...")

                    elif presence_res is not None:
                        # case 3: 只有 presence 结果
                        quality_flag = "presence_only"
                        print(f"{current_time:.2f}s\tPresence detected, no breathing yet")

                    else:
                        # case 4: 连 presence 也没有
                        quality_flag = "none"
                        print(f"{current_time:.2f}s\tNo presence")

                    # ----- Radar enter 时间（如果还没发生则为空） -----
                    radar_enter_time_val = radar_enter_time if radar_enter_time is not None else ""

                    # ----- 写一行简化后的 CSV -----
                    row = [
                        current_time,
                        unix_time,
                        quality_flag,
                        breath_rate_bpm,
                        processed_data.app_state,
                        processed_data.distances_being_analyzed,
                        presence_detected,
                        presence_distance,
                        intra_presence_score,
                        inter_presence_score,
                        presence_distance_index,
                        radar_enter_time_val,
                    ]
                    csv_writer.writerow(row)

                except et.PGProccessDiedException:
                    break

        ref_app.stop()
        print("Disconnecting...")

    client.close()
    print("Done.")


if __name__ == "__main__":
    main()
