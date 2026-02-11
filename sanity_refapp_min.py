#!/usr/bin/env python3
"""Minimal sanity check for Acconeer XM125 (A121) Breathing RefApp on Raspberry Pi."""

from __future__ import annotations

import argparse
import os
import signal
import time
from collections import Counter
from typing import Any, Optional

import numpy as np
from acconeer.exptool import a121
from acconeer.exptool.a121 import Profile
from acconeer.exptool.a121.algo.breathing import RefApp
from acconeer.exptool.a121.algo.breathing._ref_app import (
    BreathingProcessorConfig,
    RefAppConfig,
)


# =========================
# Editable conservative defaults
# =========================
START_M = 0.4
END_M = 0.7
LOWEST_BREATHING_RATE_BPM = 6
HIGHEST_BREATHING_RATE_BPM = 30
TIME_SERIES_LENGTH_S = 15
USE_PRESENCE_PROCESSOR = True
PROFILE = Profile.PROFILE_5
SWEEPS_PER_FRAME = 16
DISTANCE_DETERMINATION_DURATION_S = 5
NUM_DISTANCES_TO_ANALYZE = 3

DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_DURATION_S = 90.0
PRINT_EVERY_S = 1.0


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


def _safe_bool_or_blank(x: Any) -> Any:
    if x is None:
        return ""
    try:
        return bool(x)
    except Exception:
        return ""


def _state_to_str(state: Any) -> str:
    if state is None:
        return ""
    return str(getattr(state, "name", state))


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal XM125 breathing RefApp sanity check")
    parser.add_argument("--port", type=str, default=DEFAULT_PORT, help="Serial port, e.g. /dev/ttyUSB0")
    parser.add_argument("--sensor-id", type=int, default=1, help="Sensor ID (XM125 default: 1)")
    parser.add_argument("--duration-s", type=float, default=DEFAULT_DURATION_S, help="Run duration in seconds")
    args = parser.parse_args()

    breathing_config = BreathingProcessorConfig(
        lowest_breathing_rate=LOWEST_BREATHING_RATE_BPM,
        highest_breathing_rate=HIGHEST_BREATHING_RATE_BPM,
        time_series_length_s=TIME_SERIES_LENGTH_S,
    )

    ref_app_config = RefAppConfig(
        use_presence_processor=USE_PRESENCE_PROCESSOR,
        start_m=START_M,
        end_m=END_M,
        num_distances_to_analyze=NUM_DISTANCES_TO_ANALYZE,
        distance_determination_duration=DISTANCE_DETERMINATION_DURATION_S,
        breathing_config=breathing_config,
        profile=PROFILE,
        sweeps_per_frame=SWEEPS_PER_FRAME,
    )

    script_path = os.path.abspath(__file__)
    print("=== Sanity RefApp Start ===")
    print(f"Script_Path: {script_path}")
    print(
        "Config: "
        f"start_m={START_M}, end_m={END_M}, "
        f"lowest_bpm={LOWEST_BREATHING_RATE_BPM}, highest_bpm={HIGHEST_BREATHING_RATE_BPM}, "
        f"time_series_length_s={TIME_SERIES_LENGTH_S}, use_presence_processor={USE_PRESENCE_PROCESSOR}, "
        f"profile={PROFILE}, sweeps_per_frame={SWEEPS_PER_FRAME}, "
        f"distance_determination_duration_s={DISTANCE_DETERMINATION_DURATION_S}, "
        f"num_distances_to_analyze={NUM_DISTANCES_TO_ANALYZE}"
    )
    print(f"Port: {args.port}, Sensor_ID: {args.sensor_id}, Duration_s: {args.duration_s}")

    client: Optional[a121.Client] = None
    ref_app: Optional[RefApp] = None

    loop_dts: list[float] = []
    state_counts: Counter[str] = Counter()
    total_frames = 0
    valid_frames = 0
    first_valid_t: Optional[float] = None

    t0 = time.time()
    last_print_t = -1e9

    interrupted = False

    def _handle_sigint(_signum: int, _frame: Any) -> None:
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGINT, _handle_sigint)

    try:
        client = a121.Client.open(serial_port=args.port, override_baudrate=115200)
        ref_app = RefApp(client=client, sensor_id=args.sensor_id, ref_app_config=ref_app_config)
        ref_app.start()

        print("Connected and running. Press Ctrl-C to stop.")
        print("t_rel_s\tapp_state\tbreathing_rate_bpm\tbreathing_valid\tpresence_detected\tpresence_distance_m\tloop_dt_s")

        while not interrupted:
            t_rel = time.time() - t0
            if t_rel >= args.duration_s:
                break

            loop_t0 = time.perf_counter()
            result = ref_app.get_next()

            breathing_result = getattr(result, "breathing_result", None)
            presence_result = getattr(result, "presence_result", None)
            app_state = _state_to_str(getattr(result, "app_state", ""))

            breathing_rate_bpm: Any = ""
            breathing_valid = False
            if breathing_result is not None:
                br = _safe_float(getattr(breathing_result, "breathing_rate", None))
                if br != "":
                    breathing_rate_bpm = br
                    breathing_valid = True

            presence_detected = ""
            presence_distance_m = ""
            if presence_result is not None:
                presence_detected = _safe_bool_or_blank(getattr(presence_result, "presence_detected", None))
                presence_distance_m = _safe_float(getattr(presence_result, "presence_distance", None))

            loop_dt_s = time.perf_counter() - loop_t0

            loop_dts.append(loop_dt_s)
            total_frames += 1
            state_counts[app_state] += 1
            if breathing_valid:
                valid_frames += 1
                if first_valid_t is None:
                    first_valid_t = t_rel

            if (t_rel - last_print_t) >= PRINT_EVERY_S:
                last_print_t = t_rel
                br_str = "" if breathing_rate_bpm == "" else f"{breathing_rate_bpm:.2f}"
                pd_str = "" if presence_distance_m == "" else f"{presence_distance_m:.3f}"
                print(
                    f"{t_rel:7.2f}\t{app_state}\t{br_str}\t{breathing_valid}\t"
                    f"{presence_detected}\t{pd_str}\t{loop_dt_s:.4f}"
                )

    except Exception as exc:
        print(f"Error: {exc}")
    finally:
        if ref_app is not None:
            try:
                ref_app.stop()
            except Exception:
                pass
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    loop_dt_p95 = float(np.percentile(loop_dts, 95)) if loop_dts else float("nan")
    loop_dt_max = float(np.max(loop_dts)) if loop_dts else float("nan")
    valid_fraction = (valid_frames / total_frames) if total_frames > 0 else float("nan")

    print("\n=== Sanity Summary ===")
    print(f"time_to_first_valid_s: {'' if first_valid_t is None else f'{first_valid_t:.2f}'}")
    print(f"valid_fraction: {valid_fraction:.4f}" if not np.isnan(valid_fraction) else "valid_fraction: ")
    print(f"loop_dt_p95_s: {loop_dt_p95:.4f}" if not np.isnan(loop_dt_p95) else "loop_dt_p95_s: ")
    print(f"loop_dt_max_s: {loop_dt_max:.4f}" if not np.isnan(loop_dt_max) else "loop_dt_max_s: ")

    print("app_state_fractions:")
    if total_frames == 0:
        print("  (no frames)")
    else:
        for state, count in sorted(state_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            frac = count / total_frames
            print(f"  {state}: {frac:.4f}")


if __name__ == "__main__":
    main()
