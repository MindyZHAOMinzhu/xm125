#!/usr/bin/env python3
"""Radar/Belt feasibility analysis (converted from notebook).

Usage examples:
  python feasibility_from_notebook.py --session-dir /content --session-id 20251211_165415 --out ./out
  python feasibility_from_notebook.py --session-dir /content --session-ids 20251211_165415,20251211_131450 --out ./out
  python feasibility_from_notebook.py --session-dir /content --ids-file ids.txt --out ./out

Expected files under --session-dir:
  {SESSION_ID}_radar.csv
  {SESSION_ID}_belt.csv
  session_start_unix.txt
  human_enter_time.txt
"""

from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# Optional (only needed for metrics)
try:
    from sklearn.metrics import mean_absolute_error, mean_squared_error
except Exception:  # pragma: no cover
    mean_absolute_error = None
    mean_squared_error = None


def _summarize_quality_flag(series: pd.Series) -> str:
    """Pick the most informative Quality_Flag within a second."""
    if series.dropna().empty:
        return "none"
    priority = {
        "breathing": 3,
        "breathing_no_rate": 2,
        "presence_only": 1,
        "none": 0,
    }
    s = series.fillna("none").astype(str)
    return s.sort_values(key=lambda x: x.map(priority).fillna(0)).iloc[-1]


def _summarize_presence(series: pd.Series) -> bool:
    """True if any frame indicates presence within the second."""
    s = series.fillna(False)
    # handle strings like "True"/"False"
    if s.dtype == object:
        s = s.astype(str).str.lower().isin(["true", "1", "yes", "y"])
    return bool(s.any())


def _summarize_numeric_mean(series: pd.Series) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return float("nan")
    return float(s.mean())


def _summarize_numeric_first(series: pd.Series) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return float("nan")
    return float(s.iloc[0])


def summarize_radar_per_second(
    df_radar: pd.DataFrame,
    full_time_index=None,
    unix_col: str = "Unix_Time",
) -> pd.DataFrame:
    """Aggregate frame-level radar CSV into a per-second table.

    Returns dataframe with:
      Unix_Sec, Radar_BPM_mean, Quality_Flag, Presence
    """
    df = df_radar.copy()

    # numeric coercion
    if "Breath_Rate_BPM" in df.columns:
        df["Breath_Rate_BPM"] = pd.to_numeric(df["Breath_Rate_BPM"], errors="coerce")
    df[unix_col] = pd.to_numeric(df[unix_col], errors="coerce")

    df = df.dropna(subset=[unix_col]).copy()
    df["Unix_Sec"] = df[unix_col].round().astype("int64")

    agg = {}
    if "Breath_Rate_BPM" in df.columns:
        agg["Breath_Rate_BPM"] = _summarize_numeric_mean
    if "Quality_Flag" in df.columns:
        agg["Quality_Flag"] = _summarize_quality_flag
    if "Presence" in df.columns:
        agg["Presence"] = _summarize_presence

    radar_sec = df.groupby("Unix_Sec", as_index=True).agg(agg)

    # Reindex to desired second axis
    if full_time_index is not None:
        idx = pd.Index(full_time_index, name="Unix_Sec")
    else:
        idx = pd.Index(range(int(radar_sec.index.min()), int(radar_sec.index.max()) + 1), name="Unix_Sec")

    radar_sec = radar_sec.reindex(idx).reset_index()

    if "Breath_Rate_BPM" in radar_sec.columns:
        radar_sec = radar_sec.rename(columns={"Breath_Rate_BPM": "Radar_BPM_mean"})

    return radar_sec


def belt_step_fill(df_belt: pd.DataFrame) -> pd.DataFrame:
    """Create a per-second belt series filled by forward-fill (step)."""
    df = df_belt.copy()
    df["Belt_Breath_Rate_BPM"] = pd.to_numeric(df["Belt_Breath_Rate_BPM"], errors="coerce")
    df["Unix_Time"] = pd.to_numeric(df["Unix_Time"], errors="coerce")
    df = df.dropna(subset=["Unix_Time"]).copy()
    df["Unix_Sec"] = df["Unix_Time"].round().astype("int64")

    belt_valid = df.dropna(subset=["Unix_Sec"]).copy()
    belt_valid["Unix_Sec"] = belt_valid["Unix_Sec"].astype("int64")

    t_min = int(belt_valid["Unix_Sec"].min())
    t_max = int(belt_valid["Unix_Sec"].max())
    full_time_index = pd.Index(range(t_min, t_max + 1), name="Unix_Sec")

    belt_series = (
        belt_valid
        .sort_values("Unix_Sec")
        .set_index("Unix_Sec")["Belt_Breath_Rate_BPM"]
        .dropna()
    )

    belt_series_full = belt_series.reindex(full_time_index).ffill()

    return (
        belt_series_full
        .reset_index()
        .rename(columns={"Belt_Breath_Rate_BPM": "Belt_BPM_step"})
    )


def read_timestamps(session_dir: Path) -> tuple[float, float, int]:
    """Read session_start_unix and human_enter_time."""
    session_start_file = session_dir / "session_start_unix.txt"
    human_enter_file = session_dir / "human_enter_time.txt"
    if not session_start_file.exists():
        raise FileNotFoundError(f"Missing file: {session_start_file}")
    if not human_enter_file.exists():
        raise FileNotFoundError(f"Missing file: {human_enter_file}")

    session_start_unix = float(session_start_file.read_text().strip())
    human_enter_unix = float(human_enter_file.read_text().strip())
    human_enter_unix_sec = int(human_enter_unix)
    return session_start_unix, human_enter_unix, human_enter_unix_sec


def analyze_session(
    session_dir: Path,
    session_id: str,
    out_dir: Path | None = None,
    rolling_window: int = 10,
    stability_std_th: float = 1.0,
    show_plots: bool = False,
) -> dict:
    """Run feasibility analysis for a single session_id."""
    session_dir = Path(session_dir)

    radar_csv = session_dir / f"{session_id}_radar.csv"
    belt_csv = session_dir / f"{session_id}_belt.csv"

    if not radar_csv.exists():
        raise FileNotFoundError(f"Missing radar CSV: {radar_csv}")
    if not belt_csv.exists():
        raise FileNotFoundError(f"Missing belt CSV: {belt_csv}")

    df_radar = pd.read_csv(radar_csv)
    df_belt = pd.read_csv(belt_csv)

    session_start_unix, human_enter_unix, human_enter_unix_sec = read_timestamps(session_dir)
    human_enter_rel = human_enter_unix - session_start_unix  # seconds since session start (float)

    # Belt per-second step
    df_belt_step = belt_step_fill(df_belt)

    # Radar per-second aligned to belt secon
    df_radar_sec = summarize_radar_per_second(df_radar, full_time_index=df_belt_step["Unix_Sec"].values)

    # Merge on Unix_Sec
    df_merged_sec = pd.merge(df_belt_step, df_radar_sec, on="Unix_Sec", how="inner")

    # Derived time axis: seconds since human enter
    df_merged_sec["t_rel_from_enter"] = df_merged_sec["Unix_Sec"] - human_enter_unix_sec

    # First valid timestamps (unix sec)
    radar_first_valid_unix = (
        df_radar_sec.loc[~pd.isna(df_radar_sec.get("Radar_BPM_mean")), "Unix_Sec"].min()
        if "Radar_BPM_mean" in df_radar_sec.columns else np.nan
    )
    belt_first_valid_unix = (
        df_belt.loc[~pd.isna(df_belt["Belt_Breath_Rate_BPM"]), "Unix_Time"].round().astype("int64").min()
        if "Belt_Breath_Rate_BPM" in df_belt.columns else np.nan
    )

    cold_start_radar = float(radar_first_valid_unix - human_enter_unix_sec) if pd.notna(radar_first_valid_unix) else float("nan")
    cold_start_belt = float(belt_first_valid_unix - human_enter_unix_sec) if pd.notna(belt_first_valid_unix) else float("nan")

    # Rolling stability on radar
    if "Radar_BPM_mean" in df_merged_sec.columns:
        df_merged_sec["Radar_Rolling_Std"] = (
            df_merged_sec["Radar_BPM_mean"].rolling(rolling_window, min_periods=3).std()
        )
        stable_mask = (df_merged_sec["Radar_Rolling_Std"] < stability_std_th).fillna(False)
        radar_first_stable_unix = int(df_merged_sec.loc[stable_mask, "Unix_Sec"].iloc[0]) if stable_mask.any() else None
    else:
        df_merged_sec["Radar_Rolling_Std"] = np.nan
        radar_first_stable_unix = None

    radar_first_stable_rel = (radar_first_stable_unix - human_enter_unix_sec) if radar_first_stable_unix is not None else None

    stable_start_unix = radar_first_stable_unix if radar_first_stable_unix is not None else (int(radar_first_valid_unix) if pd.notna(radar_first_valid_unix) else int(df_merged_sec["Unix_Sec"].min()))

    df_stable = df_merged_sec[df_merged_sec["Unix_Sec"] >= stable_start_unix].copy()
    df_stable["Error"] = df_stable.get("Radar_BPM_mean") - df_stable.get("Belt_BPM_step")

    # Metrics on stable segment
    metrics = {"MAE": np.nan, "RMSE": np.nan, "CORR": np.nan}
    if "Radar_BPM_mean" in df_stable.columns and "Belt_BPM_step" in df_stable.columns:
        radar_vals = df_stable["Radar_BPM_mean"].to_numpy(dtype=float)
        belt_vals = df_stable["Belt_BPM_step"].to_numpy(dtype=float)
        mask = ~np.isnan(radar_vals) & ~np.isnan(belt_vals)
        radar_vals = radar_vals[mask]
        belt_vals = belt_vals[mask]
        if radar_vals.size > 1:
            metrics["CORR"] = float(np.corrcoef(belt_vals, radar_vals)[0, 1])
        if mean_absolute_error is not None and radar_vals.size > 0:
            metrics["MAE"] = float(mean_absolute_error(belt_vals, radar_vals))
            metrics["RMSE"] = float(np.sqrt(mean_squared_error(belt_vals, radar_vals)))

    # Output handling
    if out_dir is not None:
        out_dir = Path(out_dir) / session_id
        out_dir.mkdir(parents=True, exist_ok=True)

        # Save merged and stable data
        df_merged_sec.to_csv(out_dir / f"{session_id}_merged_per_sec.csv", index=False)
        df_stable.to_csv(out_dir / f"{session_id}_stable_segment.csv", index=False)

    # Plots
    def _maybe_save(fig, name: str):
        if out_dir is not None:
            fig.savefig(Path(out_dir) / name, dpi=200, bbox_inches="tight")
        if show_plots:
            plt.show()
        plt.close(fig)

    # 1) Time series: Radar & Belt
    fig1 = plt.figure(figsize=(10, 4))
    plt.scatter(df_merged_sec["t_rel_from_enter"], df_merged_sec["Radar_BPM_mean"], s=12, alpha=0.7, label="Radar BPM")
    plt.plot(df_merged_sec["t_rel_from_enter"], df_merged_sec["Belt_BPM_step"], alpha=0.8, label="Belt BPM (step)")
    if pd.notna(radar_first_valid_unix):
        plt.axvline(x=(radar_first_valid_unix - human_enter_unix_sec), linestyle=":", label="First Radar BPM")
    if pd.notna(belt_first_valid_unix):
        plt.axvline(x=(belt_first_valid_unix - human_enter_unix_sec), linestyle="--", label="First Belt BPM")
    if radar_first_stable_unix is not None:
        plt.axvline(x=(radar_first_stable_unix - human_enter_unix_sec), linestyle="-.", label="Radar First Stable")
    plt.xlabel("Time since enter (sec)")
    plt.ylabel("BPM")
    plt.title(f"Radar vs Belt — {session_id}")
    plt.grid(alpha=0.3)
    plt.legend()
    _maybe_save(fig1, f"{session_id}_timeseries.png")

    # 2) Rolling std
    fig2 = plt.figure(figsize=(10, 3))
    plt.plot(df_merged_sec["t_rel_from_enter"], df_merged_sec["Radar_Rolling_Std"])
    plt.axhline(stability_std_th, linestyle="--", label="Stability threshold")
    plt.xlabel("Time since enter (sec)")
    plt.ylabel("Radar rolling std (BPM)")
    plt.title("Radar stability over time")
    plt.grid(alpha=0.3)
    plt.legend()
    _maybe_save(fig2, f"{session_id}_stability.png")

    # 3) Error over time (stable segment)
    fig3 = plt.figure(figsize=(10, 3))
    plt.scatter(df_stable["t_rel_from_enter"], df_stable["Error"], s=12, alpha=0.7)
    plt.axhline(0, linestyle="--")
    plt.xlabel("Time since enter (sec)")
    plt.ylabel("Radar - Belt (BPM)")
    plt.title("Radar error over time (stable segment)")
    plt.grid(alpha=0.3)
    _maybe_save(fig3, f"{session_id}_error_over_time.png")

    # 4) Error distribution
    fig4 = plt.figure(figsize=(7, 4))
    plt.hist(df_stable["Error"].dropna(), bins=20)
    plt.axvline(0, linestyle="--")
    plt.xlabel("Error (Radar - Belt)")
    plt.ylabel("Count")
    plt.title("Error distribution (stable segment)")
    _maybe_save(fig4, f"{session_id}_error_hist.png")

    # 5) Scatter belt vs radar (stable)
    if "Radar_BPM_mean" in df_stable.columns and "Belt_BPM_step" in df_stable.columns:
        fig5 = plt.figure(figsize=(6, 6))
        plt.scatter(belt_vals, radar_vals, alpha=0.6)
        if belt_vals.size and radar_vals.size:
            max_bpm = float(max(belt_vals.max(), radar_vals.max()))
            min_bpm = float(min(belt_vals.min(), radar_vals.min()))
            plt.plot([min_bpm, max_bpm], [min_bpm, max_bpm], "r--")
        plt.xlabel("Belt BPM")
        plt.ylabel("Radar BPM")
        plt.title("Radar vs Belt — stable segment correlation")
        plt.grid(alpha=0.3)
        _maybe_save(fig5, f"{session_id}_scatter.png")

    summary = {
        "session_id": session_id,
        "session_dir": str(session_dir),
        "human_enter_rel_sec": float(human_enter_rel),
        "radar_first_valid_unix": None if pd.isna(radar_first_valid_unix) else int(radar_first_valid_unix),
        "belt_first_valid_unix": None if pd.isna(belt_first_valid_unix) else int(belt_first_valid_unix),
        "cold_start_radar_sec_since_enter": cold_start_radar,
        "cold_start_belt_sec_since_enter": cold_start_belt,
        "radar_first_stable_unix": radar_first_stable_unix,
        "radar_first_stable_sec_since_enter": radar_first_stable_rel,
        "stable_start_unix": int(stable_start_unix),
        **metrics,
    }
    return summary


def _parse_ids(session_id: str | None, session_ids: str | None, ids_file: str | None) -> list[str]:
    ids: list[str] = []
    if session_id:
        ids.append(session_id.strip())
    if session_ids:
        ids.extend([x.strip() for x in session_ids.split(",") if x.strip()])
    if ids_file:
        p = Path(ids_file)
        if not p.exists():
            raise FileNotFoundError(f"ids file not found: {p}")
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ids.append(line)
    # de-dup keep order
    seen=set()
    out=[]
    for x in ids:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-dir", required=True, help="Directory containing session files")
    ap.add_argument("--session-id", default=None, help="Single session id, e.g., 20251211_165415")
    ap.add_argument("--session-ids", default=None, help="Comma-separated session ids")
    ap.add_argument("--ids-file", default=None, help="Text file with one session id per line")
    ap.add_argument("--out", default=None, help="Output directory (will create subfolder per session)")
    ap.add_argument("--rolling-window", type=int, default=10)
    ap.add_argument("--stability-std-th", type=float, default=1.0)
    ap.add_argument("--show-plots", action="store_true", help="Show plots interactively")
    args = ap.parse_args()

    ids = _parse_ids(args.session_id, args.session_ids, args.ids_file)
    if not ids:
        raise SystemExit("No session ids provided. Use --session-id, --session-ids, or --ids-file.")

    out_dir = Path(args.out) if args.out else None
    summaries = []
    for sid in ids:
        summary = analyze_session(
            session_dir=Path(args.session_dir),
            session_id=sid,
            out_dir=out_dir,
            rolling_window=args.rolling_window,
            stability_std_th=args.stability_std_th,
            show_plots=args.show_plots,
        )
        summaries.append(summary)
        print("\n==== Summary:", sid, "====")
        for k,v in summary.items():
            print(f"{k}: {v}")

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(summaries).to_csv(out_dir / "summary_all_sessions.csv", index=False)
        print(f"\nSaved summary CSV to: {out_dir / 'summary_all_sessions.csv'}")


if __name__ == "__main__":
    main()
