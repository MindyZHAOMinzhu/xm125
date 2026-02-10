# xm125_breathing_refapp_pi_v2.py
# XM125 breathing RefApp test on Raspberry Pi -- feasibility CSV version (improved)

from __future__ import annotations

import csv
import datetime
import os
import time
import traceback
from pathlib import Path
from typing import Any, Optional

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


def _read_session_start_unix(path: Path) -> float:
    if path.exists():
        return float(path.read_text().strip())
    return time.time()


def _safe_float(x: Any) -> Any:
    if x is None:
        return ""
    try:
        v = float(x)
        if np.isnan(v):
            return ""
        return v
    except Exception:
        return ""


def _safe_bool(x: Any) -> Any:
    if x is None:
        return ""
    try:
        return bool(x)
    except Exception:
        return ""


def _format_distances(distances: Any) -> str:
    # Make CSV-friendly
    if distances is None:
        return ""
    try:
        if isinstance(distances, (list, tuple, np.ndarray)):
            arr = np.array(distances).astype(float).tolist()
            return ";".join([f"{d:.4f}" for d in arr])
        return str(distances)
    except Exception:
        return str(distances)


def main():
    parser = a121.ExampleArgumentParser()
    parser.add_argument("--prefix", type=str, default=None, help="Output filename prefix (without extension).")
    parser.add_argument("--port", type=str, default="/dev/ttyUSB0", help="Serial port, e.g., /dev/ttyUSB0")
    parser.add_argument("--sensor-id", type=int, default=1, help="Sensor ID (XM125 default is 1)")

    # Presence enter window (for enter event)
    parser.add_argument("--enter-min", type=float, default=0.4, help="Enter distance min (m)")
    parser.add_argument("--enter-max", type=float, default=0.7, help="Enter distance max (m)")
    parser.add_argument("--enter-k", type=int, default=1, help="Require K consecutive frames in range to mark enter")

    # Logging/IO behavior
    parser.add_argument("--print-every-s", type=float, default=1.0, help="Throttle console prints (seconds)")
    parser.add_argument("--flush-every-n", type=int, default=20, help="Flush CSV every N rows")

    args = parser.parse_args()
    et.utils.config_logging(args)

    # ---------- 0) read session_start_unix ----------
    session_start_path = Path("session_start_unix.txt")
    session_start_unix = _read_session_start_unix(session_start_path)
    if session_start_path.exists():
        print(f"Using session_start_unix from file: {session_start_unix}")
    else:
        print(f"No session_start_unix.txt, fallback to {session_start_unix}")

    sensor_id = args.sensor_id

    # ---------- 1) Breathing processor config ----------
    breathing_processor_config = BreathingProcessorConfig(
        # All configure for BreathingProcessorConfig()
        lowest_breathing_rate=6,
        highest_breathing_rate=30,
        time_series_length_s=15,
    )

    # ---------- 2) Presence processor config ----------
    presence_config = PresenceProcessorConfig(
        intra_detection_threshold=4,
        intra_frame_time_const=0.15,
        inter_frame_fast_cutoff=20,
        inter_frame_slow_cutoff=0.2,
        inter_frame_deviation_time_const=0.5,
    )

    # ---------- 3) RefApp config ----------
    ref_app_config = RefAppConfig(
        use_presence_processor=True,
        start_m=0.4,
        end_m=0.7,
        num_distances_to_analyze=3,
        distance_determination_duration=5,
        breathing_config=breathing_processor_config,
        presence_config=presence_config,
        profile=Profile.PROFILE_5,
        sweeps_per_frame=16,
    )

    # ---------- 4) Generate sensor_config and connect ----------
    sensor_config = get_sensor_config(ref_app_config=ref_app_config)

    client = a121.Client.open(
        serial_port=args.port,
        override_baudrate=115200,
    )
    print("✅ Connected to XM125")
    print("Server Info:")
    print(client.server_info)

    client.setup_session(sensor_config)
    print("✅ Session setup done")

    # ---------- 5) Output names ----------
    if args.prefix is not None:
        filename_prefix = f"{args.prefix}_radar"
    else:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename_prefix = f"xm125_session_{ts}_radar"

    h5file = f"{filename_prefix}.h5"
    csv_file = f"{filename_prefix}.csv"

    print(f"📄 Radar H5 will be saved to: {h5file}")
    print(f"📄 Radar CSV will be saved to: {csv_file}")

    ratio = 1.0

    last_print_t = 0.0
    frame_idx = 0

    radar_enter_time: Optional[float] = None
    enter_streak = 0

    # Try to reference the specific PG* exception if it exists in this install
    pg_exc = getattr(et, "PGProcessDiedException", None) or getattr(et, "PGProccessDiedException", None)

    try:
        with a121.H5Recorder(h5file, client):
            ref_app = RefApp(client=client, sensor_id=sensor_id, ref_app_config=ref_app_config)
            ref_app.start()

            interrupt_handler = et.utils.ExampleInterruptHandler()
            print("Press Ctrl-C to end session")

            with open(csv_file, "w", newline="") as csvfile:
                csv_writer = csv.writer(csvfile)
                csv_writer.writerow([
                    "Frame_Idx",
                    "Timestamp",
                    "Unix_Time",
                    "Quality_Flag",
                    "Breath_Rate_BPM",
                    "App_State",
                    "Distances_Being_Analyzed",
                    "Presence_Detected",
                    "Presence_Distance_m",
                    "Intra_Presence_Score",
                    "Inter_Presence_Score",
                    "Presence_Distance_Index",
                    "Radar_Enter_Time",
                ])

                while not interrupt_handler.got_signal:
                    processed_data = ref_app.get_next()

                    unix_time = time.time()
                    current_time = unix_time - session_start_unix

                    breathing_res = getattr(processed_data, "breathing_result", None)
                    presence_res = getattr(processed_data, "presence_result", None)

                    quality_flag = "none"
                    breath_rate_bpm: Any = ""

                    presence_detected: Any = ""
                    presence_distance: Any = ""
                    intra_presence_score: Any = ""
                    inter_presence_score: Any = ""
                    presence_distance_index: Any = ""

                    # ----- Presence -----
                    if presence_res is not None:
                        presence_detected = _safe_bool(getattr(presence_res, "presence_detected", None))
                        presence_distance = _safe_float(getattr(presence_res, "presence_distance", None))
                        intra_presence_score = _safe_float(getattr(presence_res, "intra_presence_score", None))
                        inter_presence_score = _safe_float(getattr(presence_res, "inter_presence_score", None))

                        extra = getattr(presence_res, "extra_result", None)
                        if extra is not None:
                            presence_distance_index = getattr(extra, "presence_distance_index", "")

                        # Enter marking with anti-jitter streak
                        in_range = (
                            presence_detected is True
                            and isinstance(presence_distance, (int, float))
                            and (args.enter_min <= presence_distance <= args.enter_max)
                        )
                        if radar_enter_time is None:
                            if in_range:
                                enter_streak += 1
                            else:
                                enter_streak = 0

                            if enter_streak >= max(1, args.enter_k):
                                radar_enter_time = current_time
                                print(f"📌 Radar enter time marked at {radar_enter_time:.2f} s (k={args.enter_k})")

                    # ----- Breathing -----
                    if breathing_res is not None:
                        br = getattr(breathing_res, "breathing_rate", None)
                        if br is not None:
                            try:
                                br_f = float(br)
                            except Exception:
                                br_f = np.nan

                            if not np.isnan(br_f):
                                quality_flag = "breathing"
                                breath_rate_bpm = br_f * ratio
                            else:
                                quality_flag = "breathing_no_rate"
                        else:
                            quality_flag = "breathing_no_rate"

                    elif presence_res is not None:
                        quality_flag = "presence_only"
                    else:
                        quality_flag = "none"

                    # ----- Throttled prints -----
                    if (current_time - last_print_t) >= args.print_every_s:
                        last_print_t = current_time
                        if quality_flag == "breathing" and isinstance(breath_rate_bpm, (int, float)):
                            print(f"{current_time:.2f}s\t{breath_rate_bpm:.2f} bpm")
                        elif quality_flag == "breathing_no_rate":
                            print(f"{current_time:.2f}s\tCalculating respiration rate...")
                        elif quality_flag == "presence_only":
                            print(f"{current_time:.2f}s\tPresence detected, no breathing yet")
                        else:
                            print(f"{current_time:.2f}s\tNo presence")

                    radar_enter_time_val: Any = radar_enter_time if radar_enter_time is not None else ""

                    row = [
                        frame_idx,
                        current_time,
                        unix_time,
                        quality_flag,
                        breath_rate_bpm,
                        getattr(processed_data, "app_state", ""),
                        _format_distances(getattr(processed_data, "distances_being_analyzed", None)),
                        presence_detected,
                        presence_distance,
                        intra_presence_score,
                        inter_presence_score,
                        presence_distance_index,
                        radar_enter_time_val,
                    ]
                    csv_writer.writerow(row)
                    frame_idx += 1

                    # ----- Safer CSV persistence -----
                    if args.flush_every_n > 0 and (frame_idx % args.flush_every_n == 0):
                        csvfile.flush()
                        try:
                            os.fsync(csvfile.fileno())
                        except Exception:
                            pass

            ref_app.stop()
            print("Disconnecting...")

    except KeyboardInterrupt:
        print("\nKeyboardInterrupt received. Stopping...")
    except Exception as e:
        # If it's the PG exception (if available), treat similarly; otherwise dump traceback.
        if pg_exc is not None and isinstance(e, pg_exc):
            print("PG process died, exiting.")
        else:
            print("❌ Exception occurred:")
            print(e)
            traceback.print_exc()
    finally:
        try:
            client.close()
        except Exception:
            pass
        print("Done.")


if __name__ == "__main__":
    main()
