import argparse
import os
import glob

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


def find_session_files(session_dir):
    """在 session 目录里自动找到 radar/belt csv 和 enter time 文件"""
    radar_csvs = glob.glob(os.path.join(session_dir, "*_radar.csv"))
    belt_csvs = glob.glob(os.path.join(session_dir, "*_belt.csv"))
    human_enter_path = os.path.join(session_dir, "human_enter_time.txt")

    if not radar_csvs:
        raise FileNotFoundError(f"No *_radar.csv found in {session_dir}")
    if not belt_csvs:
        raise FileNotFoundError(f"No *_belt.csv found in {session_dir}")

    radar_csv = radar_csvs[0]
    belt_csv = belt_csvs[0]
    human_enter = None

    if os.path.exists(human_enter_path):
        with open(human_enter_path, "r") as f:
            txt = f.read().strip()
            try:
                human_enter = float(txt)
            except ValueError:
                human_enter = None

    return radar_csv, belt_csv, human_enter


def load_radar(radar_csv, presence_dist_range=(0.4, 0.7)):
    """读取 radar csv，并提取几个关键时间点"""
    df = pd.read_csv(radar_csv)

    # 转数值
    df["Breath_Rate_BPM"] = pd.to_numeric(df["Breath_Rate_BPM"], errors="coerce")
    df["Timestamp"] = pd.to_numeric(df["Timestamp"], errors="coerce")

    # presence 距离
    if "Presence_Distance_m" in df.columns:
        df["Presence_Distance_m"] = pd.to_numeric(
            df["Presence_Distance_m"], errors="coerce"
        )

    # 第一次 presence (in range)
    t_presence = None
    if "Presence_Detected" in df.columns and "Presence_Distance_m" in df.columns:
        mask_pres = (
            (df["Presence_Detected"] == True)
            & (df["Presence_Distance_m"] >= presence_dist_range[0])
            & (df["Presence_Distance_m"] <= presence_dist_range[1])
        )
        if mask_pres.any():
            t_presence = df.loc[mask_pres, "Timestamp"].min()

    # 第一次有有效呼吸率
    mask_breath = (df["Quality_Flag"] == "breathing") & df["Breath_Rate_BPM"].notna()
    t_first_breath = df.loc[mask_breath, "Timestamp"].min() if mask_breath.any() else None

    return df, t_presence, t_first_breath


def load_belt(belt_csv):
    """读取 belt csv，并返回 DataFrame + 第一次有有效 BPM 的时间"""
    df = pd.read_csv(belt_csv)
    df["Timestamp"] = pd.to_numeric(df["Timestamp"], errors="coerce")
    df["Belt_Breath_Rate_BPM"] = pd.to_numeric(
        df["Belt_Breath_Rate_BPM"], errors="coerce"
    )

    mask_valid = df["Belt_Breath_Rate_BPM"].notna()
    t_first_belt = df.loc[mask_valid, "Timestamp"].min() if mask_valid.any() else None

    return df, t_first_belt


def merge_radar_belt(radar_df, belt_df, belt_shift_s=0.0, tolerance_s=0.5):
    """
    用 Timestamp 做 asof merge，
    belt_shift_s: 如果你觉得 belt 整体晚/早了几秒，可以用这个参数修正。
    """
    # 调整 belt 时间轴
    belt_df = belt_df.copy()
    belt_df["Timestamp_shifted"] = belt_df["Timestamp"] + belt_shift_s

    radar_sorted = radar_df.sort_values("Timestamp")
    belt_sorted = belt_df.sort_values("Timestamp_shifted")

    merged = pd.merge_asof(
        radar_sorted,
        belt_sorted,
        left_on="Timestamp",
        right_on="Timestamp_shifted",
        direction="nearest",
        tolerance=tolerance_s,
        suffixes=("_radar", "_belt"),
    )
    return merged


def compute_feasibility_metrics(
    t_presence, t_first_radar_breath, t_first_belt_breath, merged
):
    metrics = {}

    metrics["t_presence"] = t_presence
    metrics["t_first_radar_breath"] = t_first_radar_breath
    metrics["t_first_belt_breath"] = t_first_belt_breath

    if t_presence is not None and t_first_radar_breath is not None:
        metrics["radar_cold_start_from_presence"] = (
            t_first_radar_breath - t_presence
        )
    else:
        metrics["radar_cold_start_from_presence"] = None

    # 计算 radar vs belt 误差（只在两边都有有效值时）
    valid_mask = (
        merged["Breath_Rate_BPM"].notna()
        & merged["Belt_Breath_Rate_BPM"].notna()
    )
    valid = merged.loc[valid_mask]

    if not valid.empty:
        diff = valid["Breath_Rate_BPM"] - valid["Belt_Breath_Rate_BPM"]
        metrics["mean_abs_error_bpm"] = float(diff.abs().mean())
        metrics["mean_signed_error_bpm"] = float(diff.mean())
        metrics["corr_radar_belt"] = float(
            np.corrcoef(
                valid["Breath_Rate_BPM"], valid["Belt_Breath_Rate_BPM"]
            )[0, 1]
        )
        metrics["n_overlap_samples"] = int(len(valid))
    else:
        metrics["mean_abs_error_bpm"] = None
        metrics["mean_signed_error_bpm"] = None
        metrics["corr_radar_belt"] = None
        metrics["n_overlap_samples"] = 0

    return metrics


def plot_bpm(merged, session_dir, show=True):
    """画一张简单的雷达 vs belt 呼吸率曲线图"""
    plt.figure(figsize=(10, 5))
    plt.plot(
        merged["Timestamp"],
        merged["Breath_Rate_BPM"],
        label="Radar BPM",
    )
    plt.plot(
        merged["Timestamp"],
        merged["Belt_Breath_Rate_BPM"],
        label="Belt BPM",
    )
    plt.xlabel("Time (s)")
    plt.ylabel("Breathing rate (BPM)")
    plt.title(f"Radar vs Belt BPM - {os.path.basename(session_dir)}")
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    if show:
        plt.show()


def main():
    parser = argparse.ArgumentParser(
        description="Merge radar & belt data for one session and check feasibility."
    )
    parser.add_argument(
        "session_dir",
        help="Path to the session folder (e.g., session_20251211_121123)",
    )
    parser.add_argument(
        "--belt-shift-s",
        type=float,
        default=0.0,
        help="Time shift (in seconds) applied to belt Timestamp before alignment.",
    )
    parser.add_argument(
        "--presence-min",
        type=float,
        default=0.4,
        help="Min presence distance (m) for 'enter' detection.",
    )
    parser.add_argument(
        "--presence-max",
        type=float,
        default=0.7,
        help="Max presence distance (m) for 'enter' detection.",
    )
    args = parser.parse_args()

    session_dir = args.session_dir

    print(f"Analyzing session: {session_dir}")
    radar_csv, belt_csv, human_enter_unix = find_session_files(session_dir)
    print(f"  Radar CSV: {radar_csv}")
    print(f"  Belt  CSV: {belt_csv}")
    if human_enter_unix is not None:
        print(f"  Human enter unix time: {human_enter_unix}")
    else:
        print(f"  Human enter time file not found or invalid.")

    radar_df, t_presence, t_first_radar = load_radar(
        radar_csv,
        presence_dist_range=(args.presence_min, args.presence_max),
    )
    belt_df, t_first_belt = load_belt(belt_csv)

    merged = merge_radar_belt(
        radar_df, belt_df, belt_shift_s=args.belt_shift_s, tolerance_s=0.5
    )

    metrics = compute_feasibility_metrics(
        t_presence, t_first_radar, t_first_belt, merged
    )

    print("\n===== FEASIBILITY SUMMARY =====")
    print(f"t_presence (radar, in-range):       {metrics['t_presence']}")
    print(f"t_first_radar_breath:              {metrics['t_first_radar_breath']}")
    print(f"t_first_belt_breath:               {metrics['t_first_belt_breath']}")
    print(
        f"radar_cold_start_from_presence:    {metrics['radar_cold_start_from_presence']}"
    )
    print(f"n_overlap_samples:                 {metrics['n_overlap_samples']}")
    print(f"mean_abs_error_bpm:                {metrics['mean_abs_error_bpm']}")
    print(f"mean_signed_error_bpm:             {metrics['mean_signed_error_bpm']}")
    print(f"corr_radar_belt:                   {metrics['corr_radar_belt']}")

    # 画图
    try:
        plot_bpm(merged, session_dir, show=True)
    except Exception as e:
        print(f"Plotting failed: {e}")


if __name__ == "__main__":
    main()
