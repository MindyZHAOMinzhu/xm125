# xm125_breathing_refapp_pi_v2.py
# XM125 breathing RefApp test on Raspberry Pi -- feasibility CSV version (improved)

from __future__ import annotations

import csv
import datetime
import json
import os
import subprocess
import time
import traceback
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple

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
        if isinstance(distances, tuple):
            return str(distances)
        if isinstance(distances, (list, np.ndarray)):
            arr = np.array(distances).astype(float).tolist()
            return ";".join([f"{d:.4f}" for d in arr])
        return str(distances)
    except Exception:
        return str(distances)


def _get_git_commit_short() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL)
        return out.decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


def _get_distance_grid_m(sensor_config: Any) -> Optional[np.ndarray]:
    # Best-effort using official utils (names differ across versions)
    try:
        from acconeer.exptool.a121.algo._utils import get_distances_m  # type: ignore

        distances_m = get_distances_m(sensor_config)
        return np.array(distances_m, dtype=float)
    except Exception:
        try:
            from acconeer.exptool.a121.algo import get_distances_m  # type: ignore

            distances_m = get_distances_m(sensor_config)
            return np.array(distances_m, dtype=float)
        except Exception:
            return None


def _extract_array(obj: Any, attr_names: Sequence[str]) -> Optional[np.ndarray]:
    for name in attr_names:
        try:
            val = getattr(obj, name, None)
        except Exception:
            val = None
        if val is None:
            continue
        try:
            arr = np.array(val, dtype=float)
            if arr.size == 0:
                continue
            return arr
        except Exception:
            continue
    return None


def _slice_array(arr: np.ndarray, start_idx: int, end_idx: int) -> np.ndarray:
    # Try to interpret tuple as inclusive if possible, otherwise fall back to exclusive
    n = arr.size
    if n == 0:
        return arr
    s = max(0, start_idx)
    if end_idx < 0:
        return arr[s:]
    if end_idx < n:
        return arr[s : end_idx + 1]
    if end_idx <= n:
        return arr[s:end_idx]
    return arr[s:]


def _peak_ratio(psd: np.ndarray) -> Any:
    if psd.size < 2:
        return ""
    try:
        idx = np.argsort(psd)[::-1]
        peak1 = psd[idx[0]]
        peak2 = psd[idx[1]]
        if peak2 == 0:
            return ""
        return float(peak1 / peak2)
    except Exception:
        return ""


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
    distance_grid_m = _get_distance_grid_m(sensor_config)

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

    # State dwell tracking
    last_app_state: Any = None
    state_start_time: Optional[float] = None

    # Traceability metadata (to prevent running an old script or config)
    script_path = os.path.abspath(__file__)
    git_commit = _get_git_commit_short()
    run_config = {
        "profile": str(ref_app_config.profile),
        "sweeps_per_frame": ref_app_config.sweeps_per_frame,
        "start_m": ref_app_config.start_m,
        "end_m": ref_app_config.end_m,
        "num_distances_to_analyze": ref_app_config.num_distances_to_analyze,
        "distance_determination_duration": ref_app_config.distance_determination_duration,
        "breathing_rate_low": breathing_processor_config.lowest_breathing_rate,
        "breathing_rate_high": breathing_processor_config.highest_breathing_rate,
        "breathing_time_series_length_s": breathing_processor_config.time_series_length_s,
        "presence_intra_detection_threshold": presence_config.intra_detection_threshold,
        "presence_intra_frame_time_const": presence_config.intra_frame_time_const,
        "presence_inter_frame_fast_cutoff": presence_config.inter_frame_fast_cutoff,
        "presence_inter_frame_slow_cutoff": presence_config.inter_frame_slow_cutoff,
        "presence_inter_frame_deviation_time_const": presence_config.inter_frame_deviation_time_const,
        "enter_min": args.enter_min,
        "enter_max": args.enter_max,
        "enter_k": args.enter_k,
    }
    run_config_json = json.dumps(run_config, separators=(",", ":"), sort_keys=True)

    # Try to reference the specific PG* exception if it exists in this install
    pg_exc = getattr(et, "PGProcessDiedException", None) or getattr(et, "PGProccessDiedException", None)

    # One-time schema probe for introspection (prints available attributes once)
    schema_probed = False
    ref_app: Optional[RefApp] = None

    try:
        with a121.H5Recorder(h5file, client):
            ref_app = RefApp(client=client, sensor_id=sensor_id, ref_app_config=ref_app_config)
            ref_app.start()

            interrupt_handler = et.utils.ExampleInterruptHandler()
            print("Press Ctrl-C to end session")

            with open(csv_file, "w", newline="") as csvfile:
                csv_writer = csv.writer(csvfile)
                # CSV header (kept stable for offline analysis)
                csv_writer.writerow([
                    "Frame_Idx",
                    "Timestamp",
                    "Unix_Time",
                    # Loop/health diagnostics
                    "Loop_Dt_s",
                    "State_Dwell_s",
                    "Since_Enter_s",
                    "Quality_Flag",
                    "Breath_Rate_BPM",
                    # Breathing validity and convenience (Hz)
                    "Breath_Rate_Hz",
                    "Breathing_Valid",
                    "App_State",
                    "Distances_Being_Analyzed",
                    # Distances_Being_Analyzed as index range
                    "DBA_Start_Idx",
                    "DBA_End_Idx",
                    # If distance grid is available, center bin in meters
                    "Distance_Bin_Center_m",
                    "Presence_Detected",
                    "Presence_Distance_m",
                    "Intra_Presence_Score",
                    "Inter_Presence_Score",
                    # Presence curves (scalarized)
                    "Intra_Max_All",
                    "Inter_Max_All",
                    "Intra_Max_InSlice",
                    "Inter_Max_InSlice",
                    "Intra_Over_Inter",
                    "Presence_Distance_Index",
                    # Breathing PSD-derived scalars
                    "PSD_Peak_Freq_Hz",
                    "PSD_Peak_BPM",
                    "PSD_Peak_Height",
                    "PSD_Peak_Ratio_1_2",
                    "Bandpower_6_30_BPM",
                    "Radar_Enter_Time",
                    # Traceability metadata
                    "Script_Path",
                    "Run_Config_JSON",
                    "Git_Commit",
                ])

                while not interrupt_handler.got_signal:
                    loop_start = time.perf_counter()
                    processed_data = ref_app.get_next()

                    unix_time = time.time()
                    current_time = unix_time - session_start_unix

                    breathing_res = getattr(processed_data, "breathing_result", None)
                    presence_res = getattr(processed_data, "presence_result", None)

                    # One-time schema probe for diagnostics (minimal, not per-frame)
                    if not schema_probed:
                        schema_probed = True
                        try:
                            if presence_res is not None:
                                pres_attrs = [a for a in dir(presence_res) if not a.startswith("_")]
                                print(f"presence_result attrs: {pres_attrs}")
                                extra = getattr(presence_res, "extra_result", None)
                                if extra is not None:
                                    extra_attrs = [a for a in dir(extra) if not a.startswith("_")]
                                    print(f"presence_extra_result attrs: {extra_attrs}")
                            if breathing_res is not None:
                                br_attrs = [a for a in dir(breathing_res) if not a.startswith("_")]
                                print(f"breathing_result attrs: {br_attrs}")
                                extra = getattr(breathing_res, "extra_result", None)
                                if extra is not None:
                                    extra_attrs = [a for a in dir(extra) if not a.startswith("_")]
                                    print(f"breathing_extra_result attrs: {extra_attrs}")
                        except Exception:
                            pass

                    quality_flag = "none"
                    breath_rate_bpm: Any = ""

                    presence_detected: Any = ""
                    presence_distance: Any = ""
                    intra_presence_score: Any = ""
                    inter_presence_score: Any = ""
                    presence_distance_index: Any = ""

                    intra_max_all: Any = ""
                    inter_max_all: Any = ""
                    intra_max_slice: Any = ""
                    inter_max_slice: Any = ""
                    intra_over_inter: Any = ""

                    dba_start_idx: Any = ""
                    dba_end_idx: Any = ""
                    distance_bin_center_m: Any = ""

                    psd_peak_freq_hz: Any = ""
                    psd_peak_bpm: Any = ""
                    psd_peak_height: Any = ""
                    psd_peak_ratio: Any = ""
                    bandpower_6_30_bpm: Any = ""

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

                        # Presence array-derived features (scalarized from intra/inter curves)
                        intra_arr = _extract_array(presence_res, ["intra"])
                        if intra_arr is None and extra is not None:
                            intra_arr = _extract_array(extra, ["intra"])
                        inter_arr = _extract_array(presence_res, ["inter"])
                        if inter_arr is None and extra is not None:
                            inter_arr = _extract_array(extra, ["inter"])

                        if intra_arr is not None:
                            try:
                                intra_max_all = float(np.max(intra_arr))
                            except Exception:
                                pass
                        if inter_arr is not None:
                            try:
                                inter_max_all = float(np.max(inter_arr))
                            except Exception:
                                pass

                        # Distances_Being_Analyzed can be a (start_idx, end_idx) tuple
                        dba = getattr(processed_data, "distances_being_analyzed", None)
                        if isinstance(dba, tuple) and len(dba) == 2:
                            try:
                                dba_start_idx = int(dba[0])
                                dba_end_idx = int(dba[1])
                            except Exception:
                                dba_start_idx = ""
                                dba_end_idx = ""

                        # Map center bin to meters if distance grid is available
                        if isinstance(dba_start_idx, int) and isinstance(dba_end_idx, int):
                            if distance_grid_m is not None:
                                try:
                                    center_idx = int(round((dba_start_idx + dba_end_idx) / 2.0))
                                    if 0 <= center_idx < len(distance_grid_m):
                                        distance_bin_center_m = float(distance_grid_m[center_idx])
                                except Exception:
                                    pass

                            # Max within the analyzed slice
                            if intra_arr is not None:
                                try:
                                    sliced = _slice_array(intra_arr, dba_start_idx, dba_end_idx)
                                    if sliced.size > 0:
                                        intra_max_slice = float(np.max(sliced))
                                except Exception:
                                    pass
                            if inter_arr is not None:
                                try:
                                    sliced = _slice_array(inter_arr, dba_start_idx, dba_end_idx)
                                    if sliced.size > 0:
                                        inter_max_slice = float(np.max(sliced))
                                except Exception:
                                    pass

                        # Intra/Inter ratio for quick stability diagnostics
                        if isinstance(intra_presence_score, (int, float)) and isinstance(inter_presence_score, (int, float)):
                            try:
                                intra_over_inter = float(intra_presence_score / (inter_presence_score + 1e-6))
                            except Exception:
                                pass
                        elif isinstance(intra_max_slice, (int, float)) and isinstance(inter_max_slice, (int, float)):
                            try:
                                intra_over_inter = float(intra_max_slice / (inter_max_slice + 1e-6))
                            except Exception:
                                pass

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

                        # Breathing extra_result may include PSD and frequency arrays
                        extra = getattr(breathing_res, "extra_result", None)
                        if extra is not None:
                            psd = _extract_array(extra, ["psd"])
                            freqs = _extract_array(extra, ["frequencies", "freqs", "frequency"])
                            if psd is not None and freqs is not None and psd.size == freqs.size:
                                try:
                                    peak_idx = int(np.argmax(psd))
                                    psd_peak_freq_hz = float(freqs[peak_idx])
                                    psd_peak_bpm = float(psd_peak_freq_hz * 60.0)
                                    psd_peak_height = float(psd[peak_idx])
                                    psd_peak_ratio = _peak_ratio(psd)

                                    # Bandpower in 6-30 BPM (0.1-0.5 Hz) if spacing is consistent
                                    band_mask = (freqs >= (6.0 / 60.0)) & (freqs <= (30.0 / 60.0))
                                    if np.any(band_mask):
                                        bandpower_6_30_bpm = float(np.sum(psd[band_mask]))
                                except Exception:
                                    pass

                    elif presence_res is not None:
                        quality_flag = "presence_only"
                    else:
                        quality_flag = "none"

                    # Breathing validity (strict bool) and Hz conversion
                    breath_valid = isinstance(breath_rate_bpm, (int, float))
                    breath_rate_hz = (breath_rate_bpm / 60.0) if breath_valid else ""

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
                    since_enter_s = (current_time - radar_enter_time) if radar_enter_time is not None else ""

                    app_state = getattr(processed_data, "app_state", "")
                    if app_state != last_app_state:
                        last_app_state = app_state
                        state_start_time = current_time
                    state_dwell_s = (current_time - state_start_time) if state_start_time is not None else ""

                    loop_dt_s = time.perf_counter() - loop_start

                    row = [
                        frame_idx,
                        current_time,
                        unix_time,
                        loop_dt_s,
                        state_dwell_s,
                        since_enter_s,
                        quality_flag,
                        breath_rate_bpm,
                        breath_rate_hz,
                        breath_valid,
                        app_state,
                        _format_distances(getattr(processed_data, "distances_being_analyzed", None)),
                        dba_start_idx,
                        dba_end_idx,
                        distance_bin_center_m,
                        presence_detected,
                        presence_distance,
                        intra_presence_score,
                        inter_presence_score,
                        intra_max_all,
                        inter_max_all,
                        intra_max_slice,
                        inter_max_slice,
                        intra_over_inter,
                        presence_distance_index,
                        psd_peak_freq_hz,
                        psd_peak_bpm,
                        psd_peak_height,
                        psd_peak_ratio,
                        bandpower_6_30_bpm,
                        radar_enter_time_val,
                        script_path,
                        run_config_json,
                        git_commit,
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

            if ref_app is not None:
                try:
                    ref_app.stop()
                except Exception:
                    pass
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
        # Ensure session is stopped before recorder detach/close
        if ref_app is not None:
            try:
                ref_app.stop()
            except Exception:
                pass
        try:
            client.close()
        except Exception:
            pass
        print("Done.")


if __name__ == "__main__":
    main()
