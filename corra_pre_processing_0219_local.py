#!/usr/bin/env python3
"""Local runnable preprocessing for one belt+radar session.

This keeps the original core logic (belt ffill, radar 1Hz resample, enter alignment,
validity flags, merge on t_sec) and adds the three merged outputs:
- merged_ffill.csv
- merged_sparse10s.csv
- merged_uncertainty_stage1.csv
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd


def parse_number_from_text(s: str) -> float:
    m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", s)
    if not m:
        raise ValueError("No number found in text")
    return float(m.group(0))


def resolve_session_files(session_dir: Path) -> tuple[Path, Path]:
    belt_files = list(session_dir.glob("*_belt.csv"))
    radar_files = list(session_dir.glob("*_radar.csv"))
    if not belt_files:
        raise FileNotFoundError(f"No *_belt.csv in {session_dir}")
    if not radar_files:
        raise FileNotFoundError(f"No *_radar.csv in {session_dir}")
    return belt_files[0], radar_files[0]


def compute_enter_unix(session_dir: Path) -> float | None:
    enter_path = session_dir / "human_enter_time.txt"
    if not enter_path.exists():
        return None

    enter_raw = parse_number_from_text(enter_path.read_text().strip())
    if enter_raw > 1e8:
        return enter_raw

    start_path = session_dir / "session_start_unix.txt"
    if not start_path.exists():
        raise ValueError(
            "human_enter_time looks like offset seconds, but session_start_unix.txt not found"
        )
    session_start_unix = parse_number_from_text(start_path.read_text().strip())
    return session_start_unix + enter_raw


def iqr(series: pd.Series) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return np.nan
    return float(s.quantile(0.75) - s.quantile(0.25))


def bool_ratio(series: pd.Series) -> float:
    s = series.dropna()
    if s.empty:
        return np.nan
    return float((s == True).mean())


def to_bool_series(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip().str.lower()
    return s.map(
        {
            "true": True,
            "false": False,
            "1": True,
            "0": False,
            "yes": True,
            "no": False,
            "y": True,
            "n": False,
            "t": True,
            "f": False,
        }
    )


def quality_active_ratio(series: pd.Series) -> float:
    s = series.dropna().astype(str).str.strip().str.lower()
    if s.empty:
        return np.nan
    # Final-format quality labels: breathing/breathing_no_rate/presence_only
    active = s.isin({"breathing", "breathing_no_rate"})
    return float(active.mean())


def p90(series: pd.Series) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return np.nan
    return float(s.quantile(0.90))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--session-dir",
        type=Path,
        default=Path("/Users/zhaoxiaozhao/xm125/session_20260219_170622"),
        help="Path to one session folder",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        default=Path("/Users/zhaoxiaozhao/xm125/data_cleaned_local"),
        help="Root folder for cleaned outputs",
    )
    parser.add_argument(
        "--write-legacy-merged",
        action="store_true",
        help="Write legacy merged_* outputs (disabled by default).",
    )
    args = parser.parse_args()

    session_dir = args.session_dir
    out_dir = args.out_root / session_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)

    belt_path, radar_path = resolve_session_files(session_dir)

    belt = pd.read_csv(belt_path)
    radar = pd.read_csv(radar_path)

    # Belt preprocessing (unchanged logic)
    belt = belt.rename(columns={"Unix_Time": "t_unix", "Belt_Breath_Rate_BPM": "belt_rr_raw"})
    if "t_unix" not in belt.columns and "Timestamp_Unix_ms" in belt.columns:
        belt["t_unix"] = pd.to_numeric(belt["Timestamp_Unix_ms"], errors="coerce") / 1000.0
    belt["t_unix"] = pd.to_numeric(belt["t_unix"], errors="coerce")
    belt["belt_rr_raw"] = pd.to_numeric(belt["belt_rr_raw"], errors="coerce")
    belt = belt.sort_values("t_unix").reset_index(drop=True)
    belt["belt_rr_ffill"] = belt["belt_rr_raw"].ffill()
    belt = belt[["t_unix", "belt_rr_raw", "belt_rr_ffill"]].copy()
    belt["t_sec"] = belt["t_unix"].astype(int)

    # Radar preprocessing (unchanged logic)
    enter_unix = compute_enter_unix(session_dir)

    radar = radar.rename(columns={"Unix_Time": "t_unix"}).copy()
    if "t_unix" not in radar.columns and "Timestamp_Unix_ms" in radar.columns:
        radar["t_unix"] = pd.to_numeric(radar["Timestamp_Unix_ms"], errors="coerce") / 1000.0
    radar["t_unix"] = pd.to_numeric(radar["t_unix"], errors="coerce")

    if "Breath_Rate_BPM" in radar.columns:
        radar["Breath_Rate_BPM"] = pd.to_numeric(radar["Breath_Rate_BPM"], errors="coerce")
    else:
        radar["Breath_Rate_BPM"] = np.nan

    if "Breathing_Valid" not in radar.columns and "Breath_Valid" in radar.columns:
        radar["Breathing_Valid"] = radar["Breath_Valid"]
    if "Breathing_Valid" in radar.columns:
        radar["Breathing_Valid"] = to_bool_series(radar["Breathing_Valid"])

    if "Presence_Detected" in radar.columns:
        radar["Presence_Detected"] = to_bool_series(radar["Presence_Detected"])

    radar = radar.dropna(subset=["t_unix"]).sort_values("t_unix").reset_index(drop=True)

    if enter_unix is not None:
        radar["enter_unix"] = enter_unix
        radar["t_rel_enter"] = radar["t_unix"] - enter_unix
        radar["human_in_front"] = radar["t_unix"] >= enter_unix
    else:
        # Fallback path when no enter file exists.
        radar["human_in_front"] = np.nan

    radar["radar_output_valid"] = radar["Breath_Rate_BPM"].notna()
    radar["radar_presence"] = radar["Presence_Detected"] if "Presence_Detected" in radar.columns else np.nan
    radar["radar_valid"] = radar["radar_output_valid"] & (radar["radar_presence"] == True)

    if "human_in_front" in radar.columns:
        radar["phase"] = np.where(
            radar["human_in_front"] == False,
            "A_pre_enter",
            np.where(radar["radar_valid"] == True, "C_post_enter_valid", "B_post_enter_pre_valid"),
        )

    radar["t_sec"] = np.floor(radar["t_unix"]).astype(int)

    radar_sorted = radar.sort_values(
        by=["t_sec", "radar_output_valid", "t_unix"],
        ascending=[True, True, True],
    )

    radar_1hz_A = radar_sorted.drop_duplicates(subset=["t_sec"], keep="last").reset_index(drop=True)

    # Preserve all 1Hz radar columns for merged outputs.
    radar_1hz_all = radar_1hz_A.copy()

    # Frame-level full export (raw-frame granularity with derived time columns).
    radar.to_csv(out_dir / "frame_level_full.csv", index=False)

    # Frame-level 1s uncertainty features from raw frames (not from 1Hz-decimated data).
    feat_src = radar.copy()

    if "Quality_Flag" in feat_src.columns:
        feat_src["_quality_bool"] = to_bool_series(feat_src["Quality_Flag"])
    if "radar_presence" in feat_src.columns:
        feat_src["_presence_bool"] = feat_src["radar_presence"]

    agg_map = {
        "n_frames": ("t_unix", "size"),
        "n_valid": ("radar_output_valid", "sum"),
        "t_unix_mean": ("t_unix", "mean"),
    }
    if "t_rel_enter" in feat_src.columns:
        agg_map["t_rel_enter_mean"] = ("t_rel_enter", "mean")
    if "Breath_Rate_BPM" in feat_src.columns:
        agg_map["BPM_median"] = ("Breath_Rate_BPM", "median")
        agg_map["BPM_std"] = ("Breath_Rate_BPM", "std")
        agg_map["BPM_IQR"] = ("Breath_Rate_BPM", iqr)
    if "_quality_bool" in feat_src.columns:
        agg_map["quality_true_ratio"] = ("_quality_bool", bool_ratio)
    if "Quality_Flag" in feat_src.columns:
        agg_map["quality_active_ratio"] = ("Quality_Flag", quality_active_ratio)
    if "Breathing_Valid" in feat_src.columns:
        agg_map["breathing_valid_ratio"] = ("Breathing_Valid", bool_ratio)
    if "_presence_bool" in feat_src.columns:
        agg_map["presence_true_ratio"] = ("_presence_bool", bool_ratio)
    if "Presence_Distance_m" in feat_src.columns:
        agg_map["Presence_Distance_m_mean"] = ("Presence_Distance_m", "mean")
        agg_map["Presence_Distance_m_std"] = ("Presence_Distance_m", "std")
    if "PSD_Peak_Height" in feat_src.columns:
        agg_map["PSD_Peak_Height_mean"] = ("PSD_Peak_Height", "mean")
        agg_map["PSD_Peak_Height_std"] = ("PSD_Peak_Height", "std")
    if "PSD_Peak_Ratio_1_2" in feat_src.columns:
        agg_map["PSD_Peak_Ratio_1_2_mean"] = ("PSD_Peak_Ratio_1_2", "mean")
        agg_map["PSD_Peak_Ratio_1_2_std"] = ("PSD_Peak_Ratio_1_2", "std")
    if "Motion_RMS" in feat_src.columns:
        feat_src["Motion_RMS"] = pd.to_numeric(feat_src["Motion_RMS"], errors="coerce")
        motion_thr_p75 = feat_src["Motion_RMS"].quantile(0.75)
        if pd.notna(motion_thr_p75):
            feat_src["_motion_active"] = feat_src["Motion_RMS"] > motion_thr_p75
        agg_map["Motion_RMS_mean"] = ("Motion_RMS", "mean")
        agg_map["Motion_RMS_std"] = ("Motion_RMS", "std")
        agg_map["Motion_RMS_p90"] = ("Motion_RMS", p90)
    if "_motion_active" in feat_src.columns:
        agg_map["motion_active_ratio"] = ("_motion_active", bool_ratio)

    frame_level_1s_features = feat_src.groupby("t_sec", as_index=False).agg(**agg_map)
    frame_level_1s_features["valid_ratio"] = (
        frame_level_1s_features["n_valid"] / frame_level_1s_features["n_frames"]
    )
    frame_level_1s_features.to_csv(out_dir / "frame_level_1s_features.csv", index=False)

    # Keep legacy reduced 1Hz export if these columns exist.
    keep_cols_1hz = [
        "t_unix",
        "t_sec",
        "Presence_Distance_m",
        "t_rel_enter",
        "human_in_front",
        "radar_presence",
        "radar_output_valid",
        "Breath_Rate_BPM",
    ]
    keep_cols_1hz = [c for c in keep_cols_1hz if c in radar_1hz_A.columns]
    radar_1hz_A = radar_1hz_A[keep_cols_1hz].copy()

    # Save core cleaned tables.
    belt.to_csv(out_dir / "belt_clean_raw.csv", index=False)
    radar.to_csv(out_dir / "radar_clean_raw.csv", index=False)
    radar_1hz_A.to_csv(out_dir / "radar_clean_1hz.csv", index=False)

    # Merge (unchanged key/logic)
    merged_debug = radar_1hz_all.merge(
        belt,
        on="t_sec",
        how="left",
        suffixes=("", "_belt"),
    )

    if args.write_legacy_merged:
        # (A) merged_ffill.csv
        merged_ffill = merged_debug.copy()
        merged_ffill.to_csv(out_dir / "merged_ffill.csv", index=False)

        # (B) merged_sparse10s.csv
        merged_sparse10s = merged_debug.loc[merged_debug["belt_rr_raw"].notna()].copy()
        merged_sparse10s.to_csv(out_dir / "merged_sparse10s.csv", index=False)

        # (C) merged_uncertainty_stage1.csv
        time_col = "t_rel_enter" if "t_rel_enter" in merged_debug.columns else "t_unix"

        stage1_keep_exact = {
            "Motion_RMS",
            "Presence_Detected",
            "Presence_Distance_m",
            "PSD_Peak_Height",
            "PSD_Peak_Ratio_1_2",
            "Peak_To_Noise",
            "Quality_Flag",
        }
        stage1_keep = [c for c in merged_debug.columns if any(k in c for k in ["Breath", "Rate", "BPM"])]
        stage1_keep += [c for c in merged_debug.columns if c in stage1_keep_exact]
        stage1_keep += [c for c in [time_col, "start_since_human_enter_s"] if c in merged_debug.columns]
        stage1_keep = list(dict.fromkeys(stage1_keep))

        merged_uncertainty_stage1 = merged_debug.copy()
        if "t_rel_enter" in merged_uncertainty_stage1.columns:
            merged_uncertainty_stage1 = merged_uncertainty_stage1.loc[
                merged_uncertainty_stage1["t_rel_enter"] > -20
            ].copy()
        elif "start_since_human_enter_s" in merged_uncertainty_stage1.columns:
            merged_uncertainty_stage1 = merged_uncertainty_stage1.loc[
                merged_uncertainty_stage1["start_since_human_enter_s"] > -20
            ].copy()

        merged_uncertainty_stage1 = merged_uncertainty_stage1.loc[:, stage1_keep]
        merged_uncertainty_stage1.to_csv(out_dir / "merged_uncertainty_stage1.csv", index=False)

    # Decision-level full: belt-aligned table with raw-frame uncertainty features.
    decision_level_full = frame_level_1s_features.merge(
        belt[["t_sec", "belt_rr_raw", "belt_rr_ffill"]],
        on="t_sec",
        how="left",
    )
    one_hz_ref_cols = [c for c in ["t_sec", "t_rel_enter", "Breath_Rate_BPM"] if c in radar_1hz_all.columns]
    if one_hz_ref_cols:
        one_hz_ref = radar_1hz_all[one_hz_ref_cols].copy()
        if "Breath_Rate_BPM" in one_hz_ref.columns:
            one_hz_ref = one_hz_ref.rename(columns={"Breath_Rate_BPM": "Breath_Rate_BPM_1hz"})
        decision_level_full = decision_level_full.merge(one_hz_ref, on="t_sec", how="left")
    decision_level_full.to_csv(out_dir / "decision_level_full.csv", index=False)

    # Decision-level undergrad stage1: compact feature set for teaching/intro analysis.
    decision_level_undergrad = decision_level_full.copy()
    if "t_rel_enter" not in decision_level_undergrad.columns and "t_rel_enter_mean" in decision_level_undergrad.columns:
        decision_level_undergrad["t_rel_enter"] = decision_level_undergrad["t_rel_enter_mean"]
    if "t_rel_enter" in decision_level_undergrad.columns:
        # Student-friendly integer time axis while preserving raw float axis.
        decision_level_undergrad["t_rel_enter_sec"] = np.floor(
            pd.to_numeric(decision_level_undergrad["t_rel_enter"], errors="coerce")
        ).astype("Int64")
        decision_level_undergrad = decision_level_undergrad.loc[
            decision_level_undergrad["t_rel_enter"] > -20
        ].copy()
    undergrad_cols = [
        "t_sec",
        "t_rel_enter",
        "t_rel_enter_sec",
        "belt_rr_raw",
        "belt_rr_ffill",
        "Breath_Rate_BPM_1hz",
        "BPM_median",
        "BPM_std",
        "BPM_IQR",
        "n_frames",
        "n_valid",
        "valid_ratio",
        "quality_true_ratio",
        "quality_active_ratio",
        "breathing_valid_ratio",
        "presence_true_ratio",
        "Motion_RMS_mean",
        "Motion_RMS_std",
        "Motion_RMS_p90",
        "motion_active_ratio",
        "Presence_Distance_m_mean",
        "Presence_Distance_m_std",
        "PSD_Peak_Height_mean",
        "PSD_Peak_Ratio_1_2_mean",
    ]
    undergrad_cols = [c for c in undergrad_cols if c in decision_level_undergrad.columns]
    decision_level_undergrad = decision_level_undergrad[undergrad_cols].copy()
    decision_level_undergrad.to_csv(out_dir / "decision_level_undergrad_stage1.csv", index=False)

    print(f"Session: {session_dir}")
    print(f"Output : {out_dir}")
    print("Wrote: frame_level_full.csv, frame_level_1s_features.csv, decision_level_full.csv, decision_level_undergrad_stage1.csv")
    if args.write_legacy_merged:
        print("Wrote legacy: merged_ffill.csv, merged_sparse10s.csv, merged_uncertainty_stage1.csv")


if __name__ == "__main__":
    main()
