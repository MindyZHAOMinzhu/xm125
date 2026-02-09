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


# Detection of distance to mark "enter" time
ENTER_DISTANCE_MIN = 0.4
ENTER_DISTANCE_MAX = 0.7


def main():

    parser = a121.ExampleArgumentParser()
    parser.add_argument(
        "--prefix",
        type=str,
        default=None,
        help="Output filename prefix (without extension).",
    )
    args = parser.parse_args()
    et.utils.config_logging(args)

    # ---------- 0) read session_start_unix ----------
    session_start_path = Path("session_start_unix.txt")
    if session_start_path.exists():
        session_start_unix = float(session_start_path.read_text().strip())
        print(f"Using session_start_unix from file: {session_start_unix}")
    else:
        # If running the radar script standalone without this file, fall back to the current time
        session_start_unix = time.time()
        print(f"No session_start_unix.txt, fallback to {session_start_unix}")

    sensor_id = 1  # XM125 default is 1
    
    
    # ---------- 1) Breathing processor config ----------
    breathing_processor_config = BreathingProcessorConfig(
        lowest_breathing_rate=6,      # 6 bpm (~10 seconds per breath)
        highest_breathing_rate=30,    # 30 bpm (~2 seconds per breath)
        time_series_length_s=15,      # Directly related to cold start, can compare experiments later
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
        use_presence_processor=True,       # Keep it on for now, turn off if issues arise
        start_m=0.4,                       # Person approximately 0.4–0.7 m
        end_m=0.7,
        num_distances_to_analyze=3,
        distance_determination_duration=5, # Use 5s to determine the best distance bin
        breathing_config=breathing_processor_config,
        presence_config=presence_config,
        profile=Profile.PROFILE_5,         # Higher frequency resolution, suitable for near-field small movements
        sweeps_per_frame=16,               # Perform 16 sweeps per frame (can be adjusted later)
    )

    # ---------- 4) Generate sensor_config and connect to XM125 ----------
    sensor_config = get_sensor_config(ref_app_config=ref_app_config)

    serial_port = "/dev/ttyUSB0"
    client = a121.Client.open(
        serial_port=serial_port,
        override_baudrate=115200,   # Stability prioritized
    )
    print("✅ Connected to XM125")
    print("Server Info:")
    print(client.server_info)

    client.setup_session(sensor_config)
    print("✅ Session setup done")

    # ---------- 5) Record raw data (h5) + RefApp ----------
    # Filename prefix: use --prefix if provided; otherwise generate one
    if args.prefix is not None:
        filename_prefix = f"{args.prefix}_radar"
    else:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename_prefix = f"xm125_session_{ts}_radar"

    h5file = f"{filename_prefix}.h5"
    csv_file = f"{filename_prefix}.csv"

    print(f"📄 Radar H5 will be saved to: {h5file}")
    print(f"📄 Radar CSV will be saved to: {csv_file}")

    ratio = 1.0  # If you want to scale BPM overall later, you can change here

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
                "Timestamp",              # Relative time (relative to session_start_unix)
                "Unix_Time",              # Absolute unix time
                "Quality_Flag",           # "breathing", "breathing_no_rate", "presence_only", "none"
                "Breath_Rate_BPM",
                "App_State",
                "Distances_Being_Analyzed",
                "Presence_Detected",
                "Presence_Distance_m",
                "Intra_Presence_Score",
                "Inter_Presence_Score",
                "Presence_Distance_Index",
                "Radar_Enter_Time",       # Radar first detected presence in range time (seconds), empty if not detected
            ])

            while not interrupt_handler.got_signal:
                processed_data = ref_app.get_next()
                unix_time = time.time()                        # Absolute time
                current_time = unix_time - session_start_unix  # Relative seconds from session start

                try:
                    breathing_res = processed_data.breathing_result
                    presence_res = processed_data.presence_result


                    quality_flag = "none"
                    breath_rate_bpm = ""

                    presence_detected = ""
                    presence_distance = ""
                    intra_presence_score = ""
                    inter_presence_score = ""
                    presence_distance_index = ""


                    if presence_res is not None:
                        presence_detected = presence_res.presence_detected
                        presence_distance = presence_res.presence_distance
                        intra_presence_score = presence_res.intra_presence_score
                        inter_presence_score = presence_res.inter_presence_score

                        if hasattr(presence_res, "extra_result") and presence_res.extra_result is not None:
                            presence_distance_index = presence_res.extra_result.presence_distance_index

                        # ⭐ If radar_enter_time has not been recorded yet, and presence distance falls within target range, record it
                        if (
                            radar_enter_time is None
                            and presence_detected
                            and presence_distance is not None
                            and ENTER_DISTANCE_MIN <= presence_distance <= ENTER_DISTANCE_MAX
                        ):
                            radar_enter_time = current_time
                            print(f"📌 Radar enter time marked at {radar_enter_time:.2f} s")

                    # ----- Handle breathing related -----
                    if breathing_res is not None:
                        br = breathing_res.breathing_rate
                        if br:
                            # case 1: Have breathing_result and have breathing_rate
                            quality_flag = "breathing"
                            breath_rate_bpm = br * ratio
                            print(f"{current_time:.2f}s\t{breath_rate_bpm:.2f} bpm")
                        else:
                            # case 2: Have breathing_result but no rate yet
                            quality_flag = "breathing_no_rate"
                            print(f"{current_time:.2f}s\tCalculating respiration rate...")

                    elif presence_res is not None:
                        # case 3: Only presence result
                        quality_flag = "presence_only"
                        print(f"{current_time:.2f}s\tPresence detected, no breathing yet")

                    else:
                        # case 4: No presence either
                        quality_flag = "none"
                        print(f"{current_time:.2f}s\tNo presence")

                    # ----- Radar enter time (empty if not occurred yet) -----
                    radar_enter_time_val = radar_enter_time if radar_enter_time is not None else ""

                    # ----- Write a simplified CSV row -----
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
