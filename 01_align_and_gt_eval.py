#!/usr/bin/env python
import argparse
import csv
import json
import math
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import matplotlib.pyplot as plt


def to_float(val):
    if val is None:
        return np.nan
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if s == "" or s.lower() in {"nan", "none", "null"}:
        return np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


def read_csv_numeric(path: Path) -> Tuple[List[str], Dict[str, List[float]]]:
    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        data = {c: [] for c in cols}
        for row in reader:
            for c in cols:
                data[c].append(to_float(row.get(c)))
    return cols, data


def normalize_key(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def find_column(cols: List[str], candidates: List[str]) -> str:
    if not cols:
        return ""
    norm_map = {normalize_key(c): c for c in cols}
    for cand in candidates:
        c_norm = normalize_key(cand)
        if c_norm in norm_map:
            return norm_map[c_norm]
    # fuzzy: contains
    for cand in candidates:
        c_norm = normalize_key(cand)
        for k, v in norm_map.items():
            if c_norm in k:
                return v
    return ""


def load_session_times(session_dir: Path) -> Tuple[float, float]:
    session_start_path = session_dir / "session_start_unix.txt"
    human_enter_path = session_dir / "human_enter_time.txt"
    if not session_start_path.exists():
        raise FileNotFoundError(f"Missing {session_start_path}")
    if not human_enter_path.exists():
        raise FileNotFoundError(f"Missing {human_enter_path}")
    session_start = float(session_start_path.read_text().strip())
    human_enter = float(human_enter_path.read_text().strip())
    return session_start, human_enter


def detect_unix_column(cols: List[str], data: Dict[str, List[float]]) -> str:
    for c in cols:
        if "unix" in c.lower():
            vals = np.array(data.get(c, []), dtype=float)
            if np.nanmax(vals) > 1e9:
                return c
    return ""


def detect_relative_column(cols: List[str], data: Dict[str, List[float]]) -> str:
    # Look for timestamp-ish columns that are not unix
    for c in cols:
        if "time" in c.lower() or "timestamp" in c.lower():
            vals = np.array(data.get(c, []), dtype=float)
            if np.nanmax(vals) < 1e9:
                return c
    return ""


def load_radar(session_dir: Path, session_start_unix: float) -> Dict[str, np.ndarray]:
    radar_path = next(session_dir.glob("*_radar.csv"))
    cols, data = read_csv_numeric(radar_path)
    unix_col = detect_unix_column(cols, data)
    ts_col = find_column(cols, ["Timestamp", "Time", "t", "t_s", "t_sec"])

    if unix_col:
        t_unix = np.array(data[unix_col], dtype=float)
    elif ts_col:
        t_unix = session_start_unix + np.array(data[ts_col], dtype=float)
        print(f"[warn] Radar missing Unix time; using {ts_col} + session_start_unix")
    else:
        raise ValueError("Radar CSV missing timestamp columns")

    t_rel = t_unix - session_start_unix

    out = {"t_unix": t_unix, "t_rel": t_rel}
    for c in cols:
        if c not in out:
            out[c] = np.array(data[c], dtype=float)
    return out


def load_belt(session_dir: Path, session_start_unix: float) -> Dict[str, np.ndarray]:
    belt_path = next(session_dir.glob("*_belt.csv"))
    cols, data = read_csv_numeric(belt_path)

    unix_col = detect_unix_column(cols, data)
    rel_col = ""

    if unix_col:
        t_unix = np.array(data[unix_col], dtype=float)
        print(f"[info] Belt time detected: {unix_col} (unix)")
    else:
        rel_col = detect_relative_column(cols, data)
        if rel_col:
            t_unix = session_start_unix + np.array(data[rel_col], dtype=float)
            print(f"[warn] Belt time detected: {rel_col} (relative seconds); converted using session_start_unix")
        else:
            # assume 10s stride if no timestamps
            n = len(next(iter(data.values()))) if data else 0
            t_unix = session_start_unix + np.arange(n, dtype=float) * 10.0
            print("[warn] Belt time not found; assuming 10s stride from session_start_unix")

    t_rel = t_unix - session_start_unix

    out = {"t_unix": t_unix, "t_rel": t_rel}
    for c in cols:
        if c not in out:
            out[c] = np.array(data[c], dtype=float)
    return out


def phase_for_time(t_unix: np.ndarray, enter_unix: float) -> np.ndarray:
    phase = np.full(t_unix.shape, "eval", dtype=object)
    phase[t_unix < enter_unix] = "pre_enter"
    phase[(t_unix >= enter_unix) & (t_unix < enter_unix + 15.0)] = "cold_grace"
    return phase


def aggregate_1hz(t_grid: np.ndarray, t_radar: np.ndarray, values: np.ndarray) -> np.ndarray:
    out = np.full(t_grid.shape, np.nan, dtype=float)
    # assume t_grid in unix seconds (integers)
    order = np.argsort(t_radar)
    t_sorted = t_radar[order]
    v_sorted = values[order]
    idx0 = 0
    n = len(t_sorted)
    for i, t0 in enumerate(t_grid):
        t1 = t0 + 1.0
        while idx0 < n and t_sorted[idx0] < t0:
            idx0 += 1
        idx1 = idx0
        while idx1 < n and t_sorted[idx1] < t1:
            idx1 += 1
        if idx1 > idx0:
            out[i] = np.nanmedian(v_sorted[idx0:idx1])
    return out


def aggregate_radar_for_belt(t_k: float, t_radar: np.ndarray, bpm: np.ndarray) -> Tuple[float, int, float]:
    # belt window [t_k-30, t_k]
    w_start = t_k - 30.0
    w_end = t_k
    order = np.argsort(t_radar)
    t_sorted = t_radar[order]
    bpm_sorted = bpm[order]

    i0 = np.searchsorted(t_sorted, w_start, side="left")
    i1 = np.searchsorted(t_sorted, w_end, side="right")
    t_win = t_sorted[i0:i1]
    bpm_win = bpm_sorted[i0:i1]

    if t_win.size == 0:
        return np.nan, 0, 0.0

    valid = np.isfinite(bpm_win)
    valid_fraction = float(np.sum(valid)) / float(len(bpm_win)) if len(bpm_win) > 0 else 0.0

    # overlap ratio with radar window [t_r - 15, t_r]
    wr_start = t_win - 15.0
    wr_end = t_win
    inter_start = np.maximum(wr_start, w_start)
    inter_end = np.minimum(wr_end, w_end)
    inter_len = np.maximum(0.0, inter_end - inter_start)
    overlap_ratio = inter_len / 15.0

    select = (overlap_ratio >= 0.5) & valid
    if np.any(select):
        bpm_hat = float(np.nanmedian(bpm_win[select]))
        support_count = int(np.sum(select))
    else:
        bpm_hat = np.nan
        support_count = 0

    return bpm_hat, support_count, valid_fraction


def read_radar_enter_time(radar: Dict[str, np.ndarray], session_start_unix: float) -> float:
    candidates = ["Radar_Enter_Time", "RadarEnterTime", "Enter_Time"]
    col = ""
    for c in candidates:
        if c in radar:
            col = c
            break
    if not col:
        return np.nan
    vals = radar[col]
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return np.nan
    v = float(np.nanmedian(vals))
    if v > 1e9:
        return v
    return session_start_unix + v


def save_csv(path: Path, header: List[str], rows: List[List]):
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in rows:
            w.writerow(row)


def expand_sessions(items: List[str]) -> List[str]:
    out = []
    for s in items:
        for part in s.split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


def resolve_session_dir(session_id: str, base_dirs: List[Path]) -> Path:
    p = Path(session_id)
    if p.exists():
        return p.resolve() if p.is_dir() else p.parent.resolve()
    for base in base_dirs:
        d = base / f"session_{session_id}"
        if d.exists():
            return d.resolve()
        if (base / f"{session_id}_radar.csv").exists() or (base / f"{session_id}_belt.csv").exists():
            return base.resolve()
    for base in base_dirs:
        for d in base.iterdir():
            if d.is_dir() and d.name.startswith("session_"):
                if (d / f"{session_id}_radar.csv").exists() or (d / f"{session_id}_belt.csv").exists():
                    return d.resolve()
    raise FileNotFoundError(f"Could not resolve session {session_id}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True, nargs="+")
    args = parser.parse_args()

    base_dirs = [Path.cwd(), Path(__file__).resolve().parent]
    sessions = expand_sessions(args.session)

    for session_id in sessions:
        session_dir = resolve_session_dir(session_id, base_dirs)
        session_start_unix, human_enter_time = load_session_times(session_dir)

        radar = load_radar(session_dir, session_start_unix)
        belt = load_belt(session_dir, session_start_unix)

        radar_bpm_col = find_column(list(radar.keys()), ["Breath_Rate_BPM", "BPM", "BreathRateBPM"])
        belt_bpm_col = find_column(list(belt.keys()), ["Belt_Breath_Rate_BPM", "Breath_Rate_BPM", "Belt_BPM", "BPM"])

        if not radar_bpm_col:
            raise ValueError("Radar BPM column not found")
        if not belt_bpm_col:
            raise ValueError("Belt BPM column not found")

        t_start = session_start_unix
        t_end = np.nanmax([np.nanmax(radar["t_unix"]), np.nanmax(belt["t_unix"])])
        t_grid = np.arange(math.floor(t_start), math.ceil(t_end) + 1, 1.0)
        t_rel = t_grid - session_start_unix

        belt_bpm = belt[belt_bpm_col]
        belt_t = belt["t_unix"]

        # Build step/ffill belt for 1Hz
        belt_valid_mask = np.isfinite(belt_bpm)
        belt_valid_times = belt_t[belt_valid_mask]
        belt_valid_values = belt_bpm[belt_valid_mask]
        belt_step = np.full(t_grid.shape, np.nan, dtype=float)
        if belt_valid_times.size > 0:
            order = np.argsort(belt_valid_times)
            bt = belt_valid_times[order]
            bv = belt_valid_values[order]
            idx = 0
            last_val = np.nan
            for i, t in enumerate(t_grid):
                while idx < len(bt) and bt[idx] <= t:
                    last_val = bv[idx]
                    idx += 1
                belt_step[i] = last_val

        # Radar aggregates to 1Hz
        radar_t = radar["t_unix"]
        radar_bpm = radar[radar_bpm_col]
        radar_bpm_1hz = aggregate_1hz(t_grid, radar_t, radar_bpm)
        radar_valid_1hz = np.isfinite(radar_bpm_1hz)

        diag_cols = [
            "Peak_To_Noise",
            "PSD_Peak_Ratio_1_2",
            "Bandpower_6_30_BPM",
            "Motion_RMS",
            "Sweep_Energy_STD",
        ]
        diag_1hz = {}
        for c in diag_cols:
            if c in radar:
                diag_1hz[c] = aggregate_1hz(t_grid, radar_t, radar[c])
            else:
                diag_1hz[c] = np.full(t_grid.shape, np.nan, dtype=float)

        phase_1hz = phase_for_time(t_grid, human_enter_time)

        # aligned_1hz_debug.csv
        header_1hz = [
            "t_rel_enter_s",
            "belt_bpm_ffill",
            "radar_bpm_1hz",
            "radar_valid_1hz",
            "Peak_To_Noise",
            "PSD_Peak_Ratio_1_2",
            "Bandpower_6_30_BPM",
            "Motion_RMS",
            "Sweep_Energy_STD",
            "phase_1hz",
        ]
        rows_1hz = []
        for i in range(len(t_grid)):
            rows_1hz.append([
                float(t_grid[i] - human_enter_time),
                float(belt_step[i]) if np.isfinite(belt_step[i]) else "",
                float(radar_bpm_1hz[i]) if np.isfinite(radar_bpm_1hz[i]) else "",
                bool(radar_valid_1hz[i]),
                float(diag_1hz["Peak_To_Noise"][i]) if np.isfinite(diag_1hz["Peak_To_Noise"][i]) else "",
                float(diag_1hz["PSD_Peak_Ratio_1_2"][i]) if np.isfinite(diag_1hz["PSD_Peak_Ratio_1_2"][i]) else "",
                float(diag_1hz["Bandpower_6_30_BPM"][i]) if np.isfinite(diag_1hz["Bandpower_6_30_BPM"][i]) else "",
                float(diag_1hz["Motion_RMS"][i]) if np.isfinite(diag_1hz["Motion_RMS"][i]) else "",
                float(diag_1hz["Sweep_Energy_STD"][i]) if np.isfinite(diag_1hz["Sweep_Energy_STD"][i]) else "",
                phase_1hz[i],
            ])

        aligned_1hz_path = session_dir / "aligned_1hz_debug.csv"
        save_csv(aligned_1hz_path, header_1hz, rows_1hz)

        # aligned_10s.csv
        belt_tick_mask = np.isfinite(belt_bpm)
        belt_tick_times = belt_t[belt_tick_mask]
        belt_tick_vals = belt_bpm[belt_tick_mask]

        header_10s = [
            "t_rel_enter_s",
            "belt_bpm",
            "radar_bpm_hat",
            "radar_support_count",
            "radar_valid_fraction_in_support",
            "error",
            "abs_error",
            "phase",
        ]

        rows_10s = []
        for t_k, b_k in zip(belt_tick_times, belt_tick_vals):
            radar_hat, support_count, valid_frac = aggregate_radar_for_belt(t_k, radar_t, radar_bpm)
            err = radar_hat - b_k if np.isfinite(radar_hat) else np.nan
            abs_err = abs(err) if np.isfinite(err) else np.nan
            phase = phase_for_time(np.array([t_k]), human_enter_time)[0]
            rows_10s.append([
                float(t_k - human_enter_time),
                float(b_k),
                float(radar_hat) if np.isfinite(radar_hat) else "",
                int(support_count),
                float(valid_frac),
                float(err) if np.isfinite(err) else "",
                float(abs_err) if np.isfinite(abs_err) else "",
                phase,
            ])

        aligned_10s_path = session_dir / "aligned_10s.csv"
        save_csv(aligned_10s_path, header_10s, rows_10s)

        # Metrics summary (index by header to avoid unit/ordering bugs)
        col_idx = {name: i for i, name in enumerate(header_10s)}
        idx_t = col_idx["t_rel_enter_s"]
        idx_belt = col_idx["belt_bpm"]
        idx_radar = col_idx["radar_bpm_hat"]
        idx_err = col_idx["error"]
        idx_abs = col_idx["abs_error"]
        idx_phase = col_idx["phase"]

        eval_errs = []
        eval_abs = []
        eval_rows = [r for r in rows_10s if r[idx_phase] == "eval" and r[idx_radar] != ""]
        for r in eval_rows:
            err = float(r[idx_err])
            eval_errs.append(err)
            eval_abs.append(abs(err))

        if eval_errs:
            mae = float(np.mean(eval_abs))
            rmse = float(np.sqrt(np.mean(np.square(eval_errs))))
            bias = float(np.mean(eval_errs))
            n_points = int(len(eval_errs))
        else:
            mae = rmse = bias = np.nan
            n_points = 0

        # warmup markers
        radar_valid_mask = np.isfinite(radar_bpm)
        radar_after_enter = radar_t[radar_valid_mask & (radar_t >= human_enter_time)]
        time_to_first_radar_valid = float(radar_after_enter[0] - human_enter_time) if radar_after_enter.size > 0 else np.nan

        belt_after_enter = belt_tick_times[belt_tick_times >= human_enter_time]
        time_to_first_belt = float(belt_after_enter[0] - human_enter_time) if belt_after_enter.size > 0 else np.nan

        aligned_after_enter = [r for r in rows_10s if r[idx_phase] == "eval" and r[idx_radar] != ""]
        time_to_first_aligned = float(aligned_after_enter[0][idx_t]) if aligned_after_enter else np.nan

        metrics = {
            "MAE": mae,
            "RMSE": rmse,
            "bias": bias,
            "N_points": n_points,
            "time_to_first_radar_valid_after_enter": time_to_first_radar_valid,
            "time_to_first_belt_point_after_enter": time_to_first_belt,
            "time_to_first_aligned_error_point": time_to_first_aligned,
        }

        (session_dir / "metrics_summary.json").write_text(json.dumps(metrics, indent=2))

        # Figures
        figs_dir = session_dir / "figs"
        figs_dir.mkdir(exist_ok=True)

        # gt_overview_timeseries.png
        plt.figure(figsize=(12, 5))
        plt.plot(t_grid - human_enter_time, belt_step, label="Belt BPM (ffill)", color="tab:blue", linewidth=2)
        plt.plot(t_grid - human_enter_time, radar_bpm_1hz, label="Radar BPM (1Hz)", color="tab:orange", linewidth=1)
        plt.axvline(0.0, color="k", linestyle="--", label="Human enter")

        radar_enter = read_radar_enter_time(radar, session_start_unix)
        if np.isfinite(radar_enter):
            plt.axvline(radar_enter - human_enter_time, color="tab:green", linestyle=":", label="Radar enter")

        if np.isfinite(time_to_first_radar_valid):
            first_valid_time = human_enter_time + time_to_first_radar_valid
            plt.axvline(first_valid_time - human_enter_time, color="tab:red", linestyle="-.", label="First radar valid")

        plt.xlabel("Time since human enter (s)")
        plt.ylabel("BPM")
        plt.title("Ground Truth Overview")
        plt.legend(loc="best")
        plt.tight_layout()
        plt.savefig(figs_dir / "gt_overview_timeseries.png")
        plt.close()

        # gt_error_timeseries_10s.png
        plt.figure(figsize=(10, 4))
        eval_t = [r[idx_t] for r in rows_10s if r[idx_phase] == "eval" and r[idx_abs] != ""]
        eval_abs_err = [r[idx_abs] for r in rows_10s if r[idx_phase] == "eval" and r[idx_abs] != ""]
        plt.plot(eval_t, eval_abs_err, marker="o", linestyle="-")
        plt.xlabel("Time since human enter (s)")
        plt.ylabel("Abs Error (BPM)")
        plt.title("Abs Error Over Belt Ticks (Eval)")
        plt.tight_layout()
        plt.savefig(figs_dir / "gt_error_timeseries_10s.png")
        plt.close()

        # gt_scatter_radar_vs_belt.png
        plt.figure(figsize=(5, 5))
        x = [r[idx_belt] for r in rows_10s if r[idx_phase] == "eval" and r[idx_radar] != ""]
        y = [r[idx_radar] for r in rows_10s if r[idx_phase] == "eval" and r[idx_radar] != ""]
        plt.scatter(x, y, alpha=0.8)
        plt.xlabel("Belt BPM")
        plt.ylabel("Radar BPM")
        plt.title("Radar vs Belt (Eval)")
        plt.tight_layout()
        plt.savefig(figs_dir / "gt_scatter_radar_vs_belt.png")
        plt.close()

        print(f"[{session_id}] Wrote {aligned_1hz_path}")
        print(f"[{session_id}] Wrote {aligned_10s_path}")
        print(f"[{session_id}] Wrote {session_dir / 'metrics_summary.json'}")
        print(f"[{session_id}] Wrote figures in {figs_dir}")


if __name__ == "__main__":
    main()
