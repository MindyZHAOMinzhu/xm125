# dump_refapp_schema_wait_breathing.py
# Minimal script: connect XM125 -> run Breathing RefApp -> wait until breathing_result is not None
# then dump processed_data schema to JSON once (with timeout).

from __future__ import annotations

import json
import time
from pathlib import Path

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


def safe_describe(obj):
    """
    JSON-serializable schema description without dumping large arrays/contents.
    """
    out = {}
    for attr in dir(obj):
        if attr.startswith("_"):
            continue

        try:
            val = getattr(obj, attr)
        except Exception:
            continue

        if callable(val):
            continue

        if val is None or isinstance(val, (bool, int, float, str)):
            out[attr] = {"type": type(val).__name__, "value": val}
        elif isinstance(val, (tuple, list)):
            out[attr] = {"type": type(val).__name__, "length": len(val)}
        elif isinstance(val, np.ndarray):
            out[attr] = {"type": "ndarray", "shape": list(val.shape), "dtype": str(val.dtype)}
        else:
            out[attr] = {"type": type(val).__name__}

    return out


def dump_schema(processed_data, out_path: Path):
    blob = {
        "processed_data_type": type(processed_data).__name__,
        "processed_data": safe_describe(processed_data),
    }

    pr = getattr(processed_data, "presence_result", None)
    if pr is not None:
        blob["presence_result_type"] = type(pr).__name__
        blob["presence_result"] = safe_describe(pr)
        pr_extra = getattr(pr, "extra_result", None)
        if pr_extra is not None:
            blob["presence_result_extra_type"] = type(pr_extra).__name__
            blob["presence_result_extra"] = safe_describe(pr_extra)

    br = getattr(processed_data, "breathing_result", None)
    if br is not None:
        blob["breathing_result_type"] = type(br).__name__
        blob["breathing_result"] = safe_describe(br)
        br_extra = getattr(br, "extra_result", None)
        if br_extra is not None:
            blob["breathing_result_extra_type"] = type(br_extra).__name__
            blob["breathing_result_extra"] = safe_describe(br_extra)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(blob, indent=2))
    print(f"✅ Dumped schema to: {out_path}")


def main():
    serial_port = "/dev/ttyUSB0"
    sensor_id = 1
    timeout_s = 60.0
    out_path = Path("debug_schema/processed_data_schema_breathing.json")

    # Minimal configs (close to your current setup)
    breathing_processor_config = BreathingProcessorConfig(
        lowest_breathing_rate=6,
        highest_breathing_rate=30,
        time_series_length_s=15,
    )

    presence_config = PresenceProcessorConfig(
        intra_detection_threshold=4,
        intra_frame_time_const=0.15,
        inter_frame_fast_cutoff=20,
        inter_frame_slow_cutoff=0.2,
        inter_frame_deviation_time_const=0.5,
    )

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

    sensor_config = get_sensor_config(ref_app_config=ref_app_config)

    client = a121.Client.open(serial_port=serial_port, override_baudrate=115200)
    client.setup_session(sensor_config)

    ref_app = RefApp(client=client, sensor_id=sensor_id, ref_app_config=ref_app_config)
    ref_app.start()

    interrupt_handler = et.utils.ExampleInterruptHandler()
    start_time = time.time()

    dumped = False
    try:
        while not interrupt_handler.got_signal and not dumped:
            if time.time() - start_time > timeout_s:
                print(f"⚠️ Timeout: breathing_result not available within {timeout_s:.0f}s")
                break

            processed_data = ref_app.get_next()
            if processed_data is None:
                continue

            # Only dump when breathing_result becomes available
            if getattr(processed_data, "breathing_result", None) is not None:
                dump_schema(processed_data, out_path)
                dumped = True
    finally:
        try:
            ref_app.stop()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
