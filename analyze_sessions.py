#!/usr/bin/env python3
"""
Offline debugging + validation for Acconeer XM125 A121 Breathing RefApp CSV logs.
Outputs:
- session_summary.csv
- report.md
- plots/*.png

Dependencies: pandas, numpy, matplotlib
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# -----------------------------
# Utility helpers
# -----------------------------

def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None or (isinstance(x, str) and x.strip() == ""):
            return None
        v = float(x)
        if math.isnan(v):
            return None
        return v
    except Exception:
        return None


def _robust_mad(values: np.ndarray) -> float:
    if values.size == 0:
        return float("nan")
    med = np.nanmedian(values)
    return float(np.nanmedian(np.abs(values - med)))


def _parse_tuple_slice(s: Any) -> Optional[Tuple[int, int]]:
    if not isinstance(s, str):
        return None
    s = s.strip()
    if not (s.startswith("(") and s.endswith(")")):
        return None
    try:
        parts = s[1:-1].split(",")
        if len(parts) != 2:
            return None
        return int(parts[0].strip()), int(parts[1].strip())
    except Exception:
        return None


def _maybe_series(df: pd.DataFrame, col: str) -> Optional[pd.Series]:
    return df[col] if col in df.columns else None


def _fraction_true(series: pd.Series) -> float:
    # Treat truthy values as 1, else 0
    if series is None or series.size == 0:
        return float("nan")
    try:
        vals = pd.to_numeric(series, errors="coerce").values
        if np.all(np.isnan(vals)):
            return float("nan")
        return float(np.nanmean(vals))
    except Exception:
        try:
            vals = series.astype(bool).values
            return float(np.mean(vals)) if vals.size else float("nan")
        except Exception:
            return float("nan")


def _time_to_first_bpm(df: pd.DataFrame) -> float:
    # Use Since_Enter_s where Breathing_Valid is True
    if "Since_Enter_s" not in df.columns or "Breathing_Valid" not in df.columns:
        return float("nan")
    valid = df["Breathing_Valid"] == True  # noqa: E712
    if valid.any():
        ser = pd.to_numeric(df.loc[valid, "Since_Enter_s"], errors="coerce")
        if ser.notna().any():
            return float(ser.min())
    return float("nan")


def _state_occupancy(df: pd.DataFrame) -> Dict[str, float]:
    if "App_State" not in df.columns:
        return {}
    counts = df["App_State"].fillna("").astype(str).value_counts(dropna=False)
    total = float(counts.sum()) if counts.sum() > 0 else 1.0
    return {f"state_frac_{k}": float(v / total) for k, v in counts.items()}


def _max_state_dwell(df: pd.DataFrame) -> float:
    if "State_Dwell_s" not in df.columns:
        return float("nan")
    return float(pd.to_numeric(df["State_Dwell_s"], errors="coerce").max())


def _distance_stability(df: pd.DataFrame) -> float:
    # Prefer Distance_Bin_Center_m, else Presence_Distance_m
    if "Distance_Bin_Center_m" in df.columns:
        ser = pd.to_numeric(df["Distance_Bin_Center_m"], errors="coerce")
    elif "Presence_Distance_m" in df.columns:
        ser = pd.to_numeric(df["Presence_Distance_m"], errors="coerce")
    else:
        return float("nan")
    if ser.notna().sum() == 0:
        return float("nan")
    return float(np.nanstd(ser.values))


def _intra_over_inter(df: pd.DataFrame) -> float:
    if "Intra_Over_Inter" in df.columns:
        ser = pd.to_numeric(df["Intra_Over_Inter"], errors="coerce")
        return float(np.nanmedian(ser.values))
    # Fallback to ratio of Intra_Max_InSlice / Inter_Max_InSlice if present
    if "Intra_Max_InSlice" in df.columns and "Inter_Max_InSlice" in df.columns:
        intra = pd.to_numeric(df["Intra_Max_InSlice"], errors="coerce")
        inter = pd.to_numeric(df["Inter_Max_InSlice"], errors="coerce")
        ratio = intra / (inter + 1e-6)
        return float(np.nanmedian(ratio.values))
    return float("nan")


def _load_run_config_json(df: pd.DataFrame) -> Dict[str, Any]:
    if "Run_Config_JSON" not in df.columns:
        return {}
    try:
        raw = df["Run_Config_JSON"].dropna().astype(str)
        if raw.empty:
            return {}
        return json.loads(raw.iloc[0])
    except Exception:
        return {}


def _trial_windows(df: pd.DataFrame, trial_window_s: float, multi_trial: bool) -> List[Tuple[float, float]]:
    # Default: one trial per file: [Radar_Enter_Time, Radar_Enter_Time + window] if enter exists, else full file
    if "Radar_Enter_Time" not in df.columns or "Timestamp" not in df.columns:
        return [(float(df["Timestamp"].min()), float(df["Timestamp"].max()))]

    ts = pd.to_numeric(df["Timestamp"], errors="coerce")
    if ts.notna().sum() == 0:
        return [(0.0, 0.0)]

    enter_vals = pd.to_numeric(df["Radar_Enter_Time"], errors="coerce")
    enter_vals = enter_vals.dropna().unique()
    enter_vals = sorted([float(x) for x in enter_vals])

    if len(enter_vals) == 0:
        return [(float(ts.min()), float(ts.max()))]

    if not multi_trial:
        start = enter_vals[0]
        end = start + trial_window_s
        return [(start, end)]

    # Multi-trial: each unique enter time gets its own window
    windows = [(t, t + trial_window_s) for t in enter_vals]
    return windows


def _filter_window(df: pd.DataFrame, start: float, end: float) -> pd.DataFrame:
    ts = pd.to_numeric(df["Timestamp"], errors="coerce")
    return df[(ts >= start) & (ts <= end)].copy()


# -----------------------------
# Plotting helpers
# -----------------------------

def _plot_loop_dt_hist(all_loop_dt: pd.Series, output_dir: Path) -> None:
    plt.figure(figsize=(7, 4))
    vals = pd.to_numeric(all_loop_dt, errors="coerce").dropna().values
    if vals.size:
        plt.hist(vals, bins=50, color="#3b6c8a", alpha=0.8)
    plt.title("Loop_Dt_s Distribution (All Sessions)")
    plt.xlabel("Loop_Dt_s")
    plt.ylabel("Count")
    out = output_dir / "loop_dt_hist.png"
    plt.tight_layout()
    plt.savefig(out)
    plt.close()


def _plot_state_stacked(state_fracs: pd.DataFrame, output_dir: Path) -> None:
    if state_fracs.empty:
        return
    plt.figure(figsize=(10, 5))
    state_fracs.plot(kind="bar", stacked=True, ax=plt.gca(), width=0.9)
    plt.title("App_State Fractions per Session")
    plt.xlabel("Session")
    plt.ylabel("Fraction")
    plt.legend(loc="upper right", ncol=2, fontsize=8)
    out = output_dir / "state_fractions.png"
    plt.tight_layout()
    plt.savefig(out)
    plt.close()


def _plot_scatter(x, y, xlabel, ylabel, title, out_path: Path) -> None:
    plt.figure(figsize=(5, 4))
    plt.scatter(x, y, s=16, alpha=0.7, color="#2f5c7a")
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


def _plot_time_series(df: pd.DataFrame, out_path: Path, title: str) -> None:
    # Breath_Rate_BPM, Presence_Distance_m, Intra/Inter scores vs time
    plt.figure(figsize=(10, 6))
    ts = pd.to_numeric(df["Timestamp"], errors="coerce")
    if "Breath_Rate_BPM" in df.columns:
        br = pd.to_numeric(df["Breath_Rate_BPM"], errors="coerce")
        plt.plot(ts, br, label="Breath_Rate_BPM", color="#1f77b4")
    if "Presence_Distance_m" in df.columns:
        pdist = pd.to_numeric(df["Presence_Distance_m"], errors="coerce")
        plt.plot(ts, pdist, label="Presence_Distance_m", color="#ff7f0e", alpha=0.7)
    if "Intra_Presence_Score" in df.columns:
        intra = pd.to_numeric(df["Intra_Presence_Score"], errors="coerce")
        plt.plot(ts, intra, label="Intra_Presence_Score", color="#2ca02c", alpha=0.7)
    if "Inter_Presence_Score" in df.columns:
        inter = pd.to_numeric(df["Inter_Presence_Score"], errors="coerce")
        plt.plot(ts, inter, label="Inter_Presence_Score", color="#d62728", alpha=0.7)
    plt.title(title)
    plt.xlabel("Time (s)")
    plt.ylabel("Value")
    if plt.gca().get_legend_handles_labels()[0]:
        plt.legend(loc="upper right", fontsize=8)
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


# -----------------------------
# Main analysis
# -----------------------------

def analyze_file(path: Path, cfg: argparse.Namespace) -> Dict[str, Any]:
    df = pd.read_csv(path)

    # Parse Distances_Being_Analyzed if string tuple and DBA_Start_Idx/DBA_End_Idx missing
    if "DBA_Start_Idx" not in df.columns or "DBA_End_Idx" not in df.columns:
        if "Distances_Being_Analyzed" in df.columns:
            parsed = df["Distances_Being_Analyzed"].apply(_parse_tuple_slice)
            df["DBA_Start_Idx"] = parsed.apply(lambda x: x[0] if isinstance(x, tuple) else np.nan)
            df["DBA_End_Idx"] = parsed.apply(lambda x: x[1] if isinstance(x, tuple) else np.nan)

    # Session-level loop timing stats
    loop_dt = pd.to_numeric(df.get("Loop_Dt_s", pd.Series(dtype=float)), errors="coerce")
    loop_p95 = float(np.nanpercentile(loop_dt.values, 95)) if loop_dt.notna().any() else float("nan")
    loop_max = float(loop_dt.max()) if loop_dt.notna().any() else float("nan")

    # State occupancy
    state_fracs = _state_occupancy(df)
    max_dwell = _max_state_dwell(df)

    # Presence / breathing stats
    valid_fraction = _fraction_true(df.get("Breathing_Valid", pd.Series(dtype=float)))
    presence_fraction = _fraction_true(df.get("Presence_Detected", pd.Series(dtype=float)))

    br = pd.to_numeric(df.get("Breath_Rate_BPM", pd.Series(dtype=float)), errors="coerce")
    br_valid = pd.to_numeric(df.get("Breathing_Valid", pd.Series(dtype=float)), errors="coerce")
    br = br[br_valid == 1] if br_valid.size else br
    bpm_median = float(np.nanmedian(br.values)) if br.notna().any() else float("nan")
    bpm_mad = _robust_mad(br.values) if br.notna().any() else float("nan")

    time_to_first_bpm = _time_to_first_bpm(df)
    distance_std = _distance_stability(df)
    intra_over_inter_med = _intra_over_inter(df)

    # PSD-derived features if present
    psd_peak_bpm_med = float("nan")
    psd_peak_bpm_mad = float("nan")
    psd_peak_ratio_med = float("nan")
    psd_bandpower_med = float("nan")

    if "PSD_Peak_BPM" in df.columns:
        psd_peak_bpm = pd.to_numeric(df["PSD_Peak_BPM"], errors="coerce")
        if psd_peak_bpm.notna().any():
            psd_peak_bpm_med = float(np.nanmedian(psd_peak_bpm.values))
            psd_peak_bpm_mad = _robust_mad(psd_peak_bpm.values)
    if "PSD_Peak_Ratio_1_2" in df.columns:
        psd_ratio = pd.to_numeric(df["PSD_Peak_Ratio_1_2"], errors="coerce")
        if psd_ratio.notna().any():
            psd_peak_ratio_med = float(np.nanmedian(psd_ratio.values))
    if "Bandpower_6_30_BPM" in df.columns:
        band = pd.to_numeric(df["Bandpower_6_30_BPM"], errors="coerce")
        if band.notna().any():
            psd_bandpower_med = float(np.nanmedian(band.values))

    # Bad-session flags
    flag_loop_p95 = loop_p95 > cfg.loop_p95_thresh if not math.isnan(loop_p95) else False
    flag_loop_max = loop_max > cfg.loop_max_thresh if not math.isnan(loop_max) else False
    flag_valid = valid_fraction < cfg.valid_fraction_min if not math.isnan(valid_fraction) else False
    flag_ttfb = time_to_first_bpm > cfg.time_to_first_bpm_max if not math.isnan(time_to_first_bpm) else False

    flag_state_stuck = False
    if "App_State" in df.columns:
        # Identify if max dwell is in a stuck state
        stuck_states = {"NO_PRESENCE_DETECTED", "DETERMINE_DISTANCE_ESTIMATE"}
        if max_dwell > cfg.state_dwell_max:
            # Find the state at max dwell time if possible
            if "State_Dwell_s" in df.columns:
                idx = pd.to_numeric(df["State_Dwell_s"], errors="coerce").idxmax()
                if idx is not None and idx in df.index:
                    st = str(df.loc[idx, "App_State"]) if "App_State" in df.columns else ""
                    if st in stuck_states:
                        flag_state_stuck = True

    bad_session = flag_valid or flag_ttfb or flag_loop_p95 or flag_state_stuck

    # Run config fields (optional)
    run_cfg = _load_run_config_json(df)

    summary = {
        "session_file": path.name,
        "loop_p95": loop_p95,
        "loop_max": loop_max,
        "valid_fraction": valid_fraction,
        "bpm_median": bpm_median,
        "bpm_mad": bpm_mad,
        "time_to_first_bpm": time_to_first_bpm,
        "distance_std": distance_std,
        "intra_over_inter_median": intra_over_inter_med,
        "presence_fraction": presence_fraction,
        "max_state_dwell": max_dwell,
        "bad_session": bool(bad_session),
        "flag_loop_p95": bool(flag_loop_p95),
        "flag_loop_max": bool(flag_loop_max),
        "flag_valid": bool(flag_valid),
        "flag_ttfb": bool(flag_ttfb),
        "flag_state_stuck": bool(flag_state_stuck),
        "psd_peak_bpm_median": psd_peak_bpm_med,
        "psd_peak_bpm_mad": psd_peak_bpm_mad,
        "psd_peak_ratio_median": psd_peak_ratio_med,
        "bandpower_6_30_bpm_median": psd_bandpower_med,
    }
    summary.update(state_fracs)

    # Add a few run config fields if present
    for k in [
        "profile",
        "sweeps_per_frame",
        "start_m",
        "end_m",
        "num_distances_to_analyze",
        "distance_determination_duration",
        "breathing_rate_low",
        "breathing_rate_high",
    ]:
        if k in run_cfg:
            summary[f"cfg_{k}"] = run_cfg[k]

    return summary


def analyze_trials(path: Path, cfg: argparse.Namespace) -> pd.DataFrame:
    df = pd.read_csv(path)
    windows = _trial_windows(df, cfg.trial_window_s, cfg.multi_trial)
    rows = []
    for i, (start, end) in enumerate(windows):
        dft = _filter_window(df, start, end)
        if dft.empty:
            continue
        row = {
            "session_file": path.name,
            "trial_idx": i,
            "trial_start": start,
            "trial_end": end,
            "valid_fraction": _fraction_true(dft.get("Breathing_Valid", pd.Series(dtype=float))),
            "time_to_first_bpm": _time_to_first_bpm(dft),
            "distance_std": _distance_stability(dft),
            "intra_over_inter_median": _intra_over_inter(dft),
            "presence_fraction": _fraction_true(dft.get("Presence_Detected", pd.Series(dtype=float))),
            "max_state_dwell": _max_state_dwell(dft),
        }
        if "PSD_Peak_BPM" in dft.columns:
            psd_peak_bpm = pd.to_numeric(dft["PSD_Peak_BPM"], errors="coerce")
            row["psd_peak_bpm_median"] = float(np.nanmedian(psd_peak_bpm.values)) if psd_peak_bpm.notna().any() else float("nan")
        if "PSD_Peak_Ratio_1_2" in dft.columns:
            psd_ratio = pd.to_numeric(dft["PSD_Peak_Ratio_1_2"], errors="coerce")
            row["psd_peak_ratio_median"] = float(np.nanmedian(psd_ratio.values)) if psd_ratio.notna().any() else float("nan")
        if "Bandpower_6_30_BPM" in dft.columns:
            band = pd.to_numeric(dft["Bandpower_6_30_BPM"], errors="coerce")
            row["bandpower_6_30_bpm_median"] = float(np.nanmedian(band.values)) if band.notna().any() else float("nan")
        rows.append(row)
    return pd.DataFrame(rows)


def build_report(summary_df: pd.DataFrame, output_dir: Path, corr_table: pd.DataFrame) -> None:
    flagged = summary_df[summary_df["bad_session"] == True].copy()  # noqa: E712
    flagged = flagged.sort_values(by=["valid_fraction", "time_to_first_bpm"], ascending=[True, False])

    report_lines = []
    report_lines.append("# XM125 A121 Breathing RefApp Session Report\n")

    report_lines.append("## Top Flagged Sessions\n")
    if flagged.empty:
        report_lines.append("No sessions flagged by current thresholds.\n")
    else:
        for _, row in flagged.head(10).iterrows():
            reasons = []
            if row.get("flag_loop_p95"):
                reasons.append("IO slow (loop p95)")
            if row.get("flag_loop_max"):
                reasons.append("IO spikes (loop max)")
            if row.get("flag_valid"):
                reasons.append("low valid_fraction")
            if row.get("flag_ttfb"):
                reasons.append("slow time_to_first_bpm")
            if row.get("flag_state_stuck"):
                reasons.append("state stuck")
            report_lines.append(f"- {row['session_file']}: {', '.join(reasons)}")
        report_lines.append("")

    report_lines.append("## Hypothesis Validation (Correlations)\n")
    if corr_table.empty:
        report_lines.append("Correlation table could not be computed (insufficient data).\n")
    else:
        # Avoid pandas to_markdown dependency on tabulate
        report_lines.append("| feature | corr_with_valid_fraction |")
        report_lines.append("|---|---|")
        for _, row in corr_table.iterrows():
            feat = row.get("feature", "")
            corr = row.get("corr_with_valid_fraction", "")
            try:
                corr_str = f"{float(corr):.3f}"
            except Exception:
                corr_str = str(corr)
            report_lines.append(f"| {feat} | {corr_str} |")
        report_lines.append("")

    report_path = output_dir / "report.md"
    report_path.write_text("\n".join(report_lines))


def compute_correlations(summary_df: pd.DataFrame) -> pd.DataFrame:
    # Correlate valid_fraction with various features
    fields = [
        "valid_fraction",
        "time_to_first_bpm",
        "intra_over_inter_median",
        "distance_std",
        "loop_p95",
        "loop_max",
        "psd_peak_ratio_median",
        "bandpower_6_30_bpm_median",
    ]
    existing = [f for f in fields if f in summary_df.columns]
    if "valid_fraction" not in existing:
        return pd.DataFrame()
    df = summary_df[existing].copy()
    df = df.apply(pd.to_numeric, errors="coerce")
    corr = df.corr(method="pearson")
    rows = []
    for f in existing:
        if f == "valid_fraction":
            continue
        rows.append({"feature": f, "corr_with_valid_fraction": float(corr.loc[f, "valid_fraction"])})
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze XM125 A121 Breathing RefApp CSV sessions.")
    parser.add_argument("--input_dir", required=True, help="Folder containing CSV sessions")
    parser.add_argument("--output_dir", required=True, help="Output folder for summary, report, plots")
    parser.add_argument("--trial_window_s", type=float, default=60.0, help="Trial window length (s)")
    parser.add_argument("--multi_trial", action="store_true", help="Treat multiple enter events as separate trials")

    # Thresholds for bad-session flags
    parser.add_argument("--loop_p95_thresh", type=float, default=0.2, help="Flag if loop p95 exceeds (s)")
    parser.add_argument("--loop_max_thresh", type=float, default=0.5, help="Flag if loop max exceeds (s)")
    parser.add_argument("--valid_fraction_min", type=float, default=0.3, help="Flag if valid_fraction below")
    parser.add_argument("--time_to_first_bpm_max", type=float, default=50.0, help="Flag if time_to_first_bpm above (s)")
    parser.add_argument("--state_dwell_max", type=float, default=15.0, help="Flag if max dwell in stuck states above (s)")

    args = parser.parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    plots_dir = output_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    csv_files = sorted([p for p in input_dir.iterdir() if p.suffix.lower() == ".csv"])
    if not csv_files:
        raise SystemExit(f"No CSV files found in {input_dir}")

    # Per-session summary
    summaries = []
    all_loop_dt = []
    state_frac_rows = []
    for p in csv_files:
        try:
            summaries.append(analyze_file(p, args))
        except Exception as e:
            print(f"Failed to analyze {p.name}: {e}")
            continue
        df = pd.read_csv(p)
        if "Loop_Dt_s" in df.columns:
            all_loop_dt.append(df["Loop_Dt_s"])
        # Collect state fractions for stacked bar
        st = _state_occupancy(df)
        if st:
            st["session_file"] = p.name
            state_frac_rows.append(st)

    summary_df = pd.DataFrame(summaries)
    summary_path = output_dir / "session_summary.csv"
    summary_df.to_csv(summary_path, index=False)

    # Per-trial summary
    trial_rows = []
    for p in csv_files:
        try:
            trial_rows.append(analyze_trials(p, args))
        except Exception as e:
            print(f"Failed to analyze trials in {p.name}: {e}")
            continue
    if trial_rows:
        trial_df = pd.concat(trial_rows, ignore_index=True)
        trial_df.to_csv(output_dir / "trial_summary.csv", index=False)

    # Plots
    if all_loop_dt:
        _plot_loop_dt_hist(pd.concat(all_loop_dt, ignore_index=True), plots_dir)

    if state_frac_rows:
        state_df = pd.DataFrame(state_frac_rows).fillna(0.0).set_index("session_file")
        _plot_state_stacked(state_df, plots_dir)

    # Scatter plots for hypotheses
    if not summary_df.empty:
        if "intra_over_inter_median" in summary_df.columns:
            _plot_scatter(
                summary_df["intra_over_inter_median"],
                summary_df["valid_fraction"],
                "intra_over_inter_median",
                "valid_fraction",
                "H2: valid_fraction vs intra_over_inter",
                plots_dir / "scatter_valid_vs_intra_over_inter.png",
            )
        if "distance_std" in summary_df.columns:
            _plot_scatter(
                summary_df["distance_std"],
                summary_df["valid_fraction"],
                "distance_std",
                "valid_fraction",
                "H3: valid_fraction vs distance_std",
                plots_dir / "scatter_valid_vs_distance_std.png",
            )
        if "loop_p95" in summary_df.columns:
            _plot_scatter(
                summary_df["loop_p95"],
                summary_df["valid_fraction"],
                "loop_p95",
                "valid_fraction",
                "H5: valid_fraction vs loop_p95",
                plots_dir / "scatter_valid_vs_loop_p95.png",
            )

    # Time series plots for worst sessions
    if not summary_df.empty:
        worst = summary_df.sort_values(by=["valid_fraction", "time_to_first_bpm"], ascending=[True, False])
        for _, row in worst.head(3).iterrows():
            p = input_dir / row["session_file"]
            if p.exists():
                dfx = pd.read_csv(p)
                out = plots_dir / f"timeseries_{p.stem}.png"
                _plot_time_series(dfx, out, f"{p.name} Time Series")

    # Correlation table and report
    corr_table = compute_correlations(summary_df)
    build_report(summary_df, output_dir, corr_table)

    print(f"Wrote session summary to {summary_path}")
    print(f"Wrote report to {output_dir / 'report.md'}")
    print(f"Plots saved to {plots_dir}")


if __name__ == "__main__":
    main()
