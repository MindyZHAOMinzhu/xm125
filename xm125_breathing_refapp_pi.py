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


EPS = 1e-9


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


def _as_float_array(x: Any) -> Optional[np.ndarray]:
    if x is None:
        return None
    try:
        arr = np.asarray(x, dtype=float)
        if arr.size == 0:
            return None
        return arr
    except Exception:
        return None


def _last_finite(arr: Optional[np.ndarray]) -> Any:
    if arr is None or arr.size == 0:
        return ""
    try:
        finite_vals = arr[np.isfinite(arr)]
        if finite_vals.size == 0:
            return ""
        return float(finite_vals[-1])
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

    ref_app: Optional[RefApp] = None

    try:
        with a121.H5Recorder(h5file, client):
            ref_app = RefApp(client=client, sensor_id=sensor_id, ref_app_config=ref_app_config)
            ref_app.start()

            interrupt_handler = et.utils.ExampleInterruptHandler()
            print("Press Ctrl-C to end session")

            with open(csv_file, "w", newline="") as csvfile:
                csv_writer = csv.writer(csvfile)
                csv_writer.writerow([
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
                    "Intra_Max_All",
                    "Inter_Max_All",
                    "Intra_Over_Inter_Max",
                    "Signal_Peak_Bin",
                    "Signal_Peak_Value",
                    "Noise_Median",
                    "Peak_To_Noise",
                    "Signal_At_PresenceBin",
                    "Noise_At_PresenceBin",
                    "PresenceBin_To_Noise",
                    "FastSlow_Diff_Max",
                    "FastSlow_Diff_AtPresenceBin",
                    "Frame_Energy",
                    "Sweep_Energy_STD",
                    "Sweep_Energy_P2P",
                    "Bin_Energy_STD",
                    "PSD_Peak_Idx",
                    "PSD_Peak_Freq_Hz",
                    "PSD_Peak_BPM",
                    "PSD_Peak_Height",
                    "PSD_Peak_Ratio_1_2",
                    "Bandpower_6_30_BPM",
                    "Motion_RMS",
                    "Motion_P2P",
                    "Rate_Hist_Last",
                    "Rate_Hist_Valid_Frac_10s",
                    "Buffer_Coverage_s",
                    "DBA_Start_Idx",
                    "DBA_End_Idx",
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

                    intra_max_all: Any = ""
                    inter_max_all: Any = ""
                    intra_over_inter_max: Any = ""

                    signal_peak_bin: Any = ""
                    signal_peak_value: Any = ""
                    noise_median: Any = ""
                    peak_to_noise: Any = ""
                    signal_at_presence_bin: Any = ""
                    noise_at_presence_bin: Any = ""
                    presencebin_to_noise: Any = ""

                    fastslow_diff_max: Any = ""
                    fastslow_diff_at_presence_bin: Any = ""

                    frame_energy: Any = ""
                    sweep_energy_std: Any = ""
                    sweep_energy_p2p: Any = ""
                    bin_energy_std: Any = ""

                    psd_peak_idx: Any = ""
                    psd_peak_freq_hz: Any = ""
                    psd_peak_bpm: Any = ""
                    psd_peak_height: Any = ""
                    psd_peak_ratio_1_2: Any = ""
                    bandpower_6_30_bpm: Any = ""

                    motion_rms: Any = ""
                    motion_p2p: Any = ""
                    rate_hist_last: Any = ""
                    rate_hist_valid_frac_10s: Any = ""
                    buffer_coverage_s: Any = ""

                    dba_start_idx: Any = ""
                    dba_end_idx: Any = ""

                    # ----- Presence -----
                    if presence_res is not None:
                        presence_detected = _safe_bool(getattr(presence_res, "presence_detected", None))
                        presence_distance = _safe_float(getattr(presence_res, "presence_distance", None))
                        intra_presence_score = _safe_float(getattr(presence_res, "intra_presence_score", None))
                        inter_presence_score = _safe_float(getattr(presence_res, "inter_presence_score", None))

                        presence_extra = getattr(presence_res, "extra_result", None)
                        if presence_extra is not None:
                            presence_distance_index = getattr(presence_extra, "presence_distance_index", "")

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

                        intra_arr = _as_float_array(getattr(presence_res, "intra", None))
                        inter_arr = _as_float_array(getattr(presence_res, "inter", None))

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
                        if isinstance(intra_max_all, (int, float)) and isinstance(inter_max_all, (int, float)):
                            try:
                                intra_over_inter_max = float(intra_max_all / (inter_max_all + EPS))
                            except Exception:
                                pass

                        abs_mean_sweep = _as_float_array(getattr(presence_extra, "abs_mean_sweep", None))
                        lp_noise = _as_float_array(getattr(presence_extra, "lp_noise", None))
                        fast_lp_mean_sweep = _as_float_array(getattr(presence_extra, "fast_lp_mean_sweep", None))
                        slow_lp_mean_sweep = _as_float_array(getattr(presence_extra, "slow_lp_mean_sweep", None))

                        frame = getattr(presence_extra, "frame", None)

                        if abs_mean_sweep is not None:
                            try:
                                signal_peak_bin = int(np.argmax(abs_mean_sweep))
                                signal_peak_value = float(np.max(abs_mean_sweep))
                            except Exception:
                                pass

                        if lp_noise is not None:
                            try:
                                noise_median = float(np.median(lp_noise))
                            except Exception:
                                pass

                        if isinstance(signal_peak_value, (int, float)) and isinstance(noise_median, (int, float)):
                            try:
                                peak_to_noise = float(signal_peak_value / (noise_median + EPS))
                            except Exception:
                                pass

                        idx = None
                        try:
                            idx = int(presence_distance_index)
                        except Exception:
                            idx = None

                        if idx is not None:
                            try:
                                if abs_mean_sweep is not None and 0 <= idx < abs_mean_sweep.size:
                                    signal_at_presence_bin = float(abs_mean_sweep[idx])
                                if lp_noise is not None and 0 <= idx < lp_noise.size:
                                    noise_at_presence_bin = float(lp_noise[idx])
                            except Exception:
                                pass

                        if isinstance(signal_at_presence_bin, (int, float)) and isinstance(noise_at_presence_bin, (int, float)):
                            try:
                                presencebin_to_noise = float(signal_at_presence_bin / (noise_at_presence_bin + EPS))
                            except Exception:
                                pass

                        if fast_lp_mean_sweep is not None and slow_lp_mean_sweep is not None:
                            try:
                                if fast_lp_mean_sweep.size == slow_lp_mean_sweep.size:
                                    diff = np.abs(fast_lp_mean_sweep - slow_lp_mean_sweep)
                                    fastslow_diff_max = float(np.max(diff))
                                    if idx is not None and 0 <= idx < diff.size:
                                        fastslow_diff_at_presence_bin = float(diff[idx])
                            except Exception:
                                pass

                        if frame is not None:
                            try:
                                a = np.abs(np.asarray(frame))
                                if a.ndim == 2 and a.size > 0:
                                    energy = a**2
                                    frame_energy = float(np.mean(energy))
                                    sweep_energy = np.mean(energy, axis=1)
                                    bin_energy = np.mean(energy, axis=0)
                                    sweep_energy_std = float(np.std(sweep_energy))
                                    sweep_energy_p2p = float(np.max(sweep_energy) - np.min(sweep_energy))
                                    bin_energy_std = float(np.std(bin_energy))
                            except Exception:
                                pass

                    # Distances_Being_Analyzed tuple parsing
                    dba = getattr(processed_data, "distances_being_analyzed", None)
                    if isinstance(dba, tuple) and len(dba) == 2:
                        try:
                            dba_start_idx = int(dba[0])
                            dba_end_idx = int(dba[1])
                        except Exception:
                            dba_start_idx = ""
                            dba_end_idx = ""

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

                        breathing_extra = getattr(breathing_res, "extra_result", None)
                        if breathing_extra is not None:
                            psd = _as_float_array(getattr(breathing_extra, "psd", None))
                            frequencies = _as_float_array(getattr(breathing_extra, "frequencies", None))

                            if psd is not None and frequencies is not None and psd.size == frequencies.size:
                                try:
                                    psd_peak_idx = int(np.argmax(psd))
                                    psd_peak_freq_hz = float(frequencies[psd_peak_idx])
                                    psd_peak_bpm = float(psd_peak_freq_hz * 60.0)
                                    psd_peak_height = float(psd[psd_peak_idx])

                                    if psd.size >= 2:
                                        top2_idx = np.argpartition(psd, -2)[-2:]
                                        top2_vals = np.sort(psd[top2_idx])[::-1]
                                        psd_peak_ratio_1_2 = float(top2_vals[0] / (top2_vals[1] + EPS))

                                    band_mask = (frequencies >= (6.0 / 60.0)) & (frequencies <= (30.0 / 60.0))
                                    if np.any(band_mask):
                                        bandpower_6_30_bpm = float(np.sum(psd[band_mask]))
                                except Exception:
                                    pass

                            breathing_motion = _as_float_array(getattr(breathing_extra, "breathing_motion", None))
                            if breathing_motion is not None:
                                try:
                                    motion_rms = float(np.sqrt(np.mean(breathing_motion**2)))
                                    motion_p2p = float(np.max(breathing_motion) - np.min(breathing_motion))
                                except Exception:
                                    pass

                            time_vector = _as_float_array(getattr(breathing_extra, "time_vector", None))
                            breathing_rate_history = _as_float_array(getattr(breathing_extra, "breathing_rate_history", None))

                            rate_hist_last = _last_finite(breathing_rate_history)

                            if time_vector is not None and time_vector.size > 1:
                                try:
                                    buffer_coverage_s = float(time_vector[-1] - time_vector[0])
                                except Exception:
                                    pass

                            if (
                                time_vector is not None
                                and breathing_rate_history is not None
                                and time_vector.size == breathing_rate_history.size
                                and time_vector.size > 0
                            ):
                                try:
                                    t_last = float(time_vector[-1])
                                    mask = (t_last - time_vector) <= 10.0
                                    n = int(np.sum(mask))
                                    if n > 0:
                                        valid_n = int(np.sum(np.isfinite(breathing_rate_history[mask])))
                                        rate_hist_valid_frac_10s = float(valid_n / n)
                                except Exception:
                                    pass

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
                        intra_max_all,
                        inter_max_all,
                        intra_over_inter_max,
                        signal_peak_bin,
                        signal_peak_value,
                        noise_median,
                        peak_to_noise,
                        signal_at_presence_bin,
                        noise_at_presence_bin,
                        presencebin_to_noise,
                        fastslow_diff_max,
                        fastslow_diff_at_presence_bin,
                        frame_energy,
                        sweep_energy_std,
                        sweep_energy_p2p,
                        bin_energy_std,
                        psd_peak_idx,
                        psd_peak_freq_hz,
                        psd_peak_bpm,
                        psd_peak_height,
                        psd_peak_ratio_1_2,
                        bandpower_6_30_bpm,
                        motion_rms,
                        motion_p2p,
                        rate_hist_last,
                        rate_hist_valid_frac_10s,
                        buffer_coverage_s,
                        dba_start_idx,
                        dba_end_idx,
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
        if pg_exc is not None and isinstance(e, pg_exc):
            print("PG process died, exiting.")
        else:
            print("❌ Exception occurred:")
            print(e)
            traceback.print_exc()
    finally:
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
