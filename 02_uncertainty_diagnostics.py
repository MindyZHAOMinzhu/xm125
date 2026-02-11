#!/usr/bin/env python
import argparse
import csv
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


def read_csv_mixed(path: Path) -> Tuple[List[str], List[dict]]:
    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        rows = []
        for row in reader:
            rows.append({k: row.get(k) for k in cols})
    return cols, rows


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
    for cand in candidates:
        c_norm = normalize_key(cand)
        for k, v in norm_map.items():
            if c_norm in k:
                return v
    return ""


def rankdata(a: np.ndarray) -> np.ndarray:
    # average ranks for ties
    sorter = np.argsort(a)
    inv = np.empty_like(sorter)
    inv[sorter] = np.arange(len(a))
    a_sorted = a[sorter]
    ranks = np.zeros_like(a_sorted, dtype=float)
    i = 0
    while i < len(a_sorted):
        j = i
        while j + 1 < len(a_sorted) and a_sorted[j + 1] == a_sorted[i]:
            j += 1
        rank = 0.5 * (i + j) + 1.0
        ranks[i:j + 1] = rank
        i = j + 1
    return ranks[inv]


def spearman_corr(x: np.ndarray, y: np.ndarray) -> float:
    mask = np.isfinite(x) & np.isfinite(y)
    if np.sum(mask) < 3:
        return np.nan
    rx = rankdata(x[mask])
    ry = rankdata(y[mask])
    if rx.size < 3:
        return np.nan
    return float(np.corrcoef(rx, ry)[0, 1])


def aggregate_window(t_k: float, t_radar: np.ndarray, features: Dict[str, np.ndarray]) -> Dict[str, float]:
    w_start = t_k - 30.0
    w_end = t_k
    order = np.argsort(t_radar)
    t_sorted = t_radar[order]

    i0 = np.searchsorted(t_sorted, w_start, side="left")
    i1 = np.searchsorted(t_sorted, w_end, side="right")

    out = {}
    for name, arr in features.items():
        v = arr[order][i0:i1]
        out[name] = float(np.nanmedian(v)) if v.size > 0 else np.nan
    out["feature_coverage_count"] = int(i1 - i0)
    return out


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

        aligned_10s_path = session_dir / "aligned_10s.csv"
        aligned_1hz_path = session_dir / "aligned_1hz_debug.csv"
        radar_path = next(session_dir.glob("*_radar.csv"))
        session_start_path = session_dir / "session_start_unix.txt"
        human_enter_path = session_dir / "human_enter_time.txt"
        session_start_unix = float(session_start_path.read_text().strip()) if session_start_path.exists() else None
        human_enter_time = float(human_enter_path.read_text().strip()) if human_enter_path.exists() else None

        # load aligned_10s
        cols_10s, data_10s = read_csv_numeric(aligned_10s_path)
        t_col = find_column(cols_10s, ["t_rel_enter_s"])
        if not t_col:
            raise ValueError("aligned_10s.csv missing t_rel_enter_s")
        t_k_rel = np.array(data_10s[t_col], dtype=float)
        if human_enter_time is None:
            raise ValueError("human_enter_time.txt required when aligned_10s uses relative time")
        t_k_unix = t_k_rel + human_enter_time
        phase = [row for row in read_csv_mixed(aligned_10s_path)[1]]
        phase_col = find_column(cols_10s, ["phase"])
        if not phase_col:
            raise ValueError("aligned_10s.csv missing phase column")

        belt_bpm = np.array(data_10s[find_column(cols_10s, ["belt_bpm", "belt"])], dtype=float)
        radar_bpm_hat = np.array(data_10s[find_column(cols_10s, ["radar_bpm_hat", "radar"])], dtype=float)
        abs_error = np.array(data_10s[find_column(cols_10s, ["abs_error", "abs"])], dtype=float)

        # load radar
        radar_cols, radar_data = read_csv_numeric(radar_path)
        t_radar_col = find_column(radar_cols, ["Unix_Time", "unix"])
        if t_radar_col:
            t_radar = np.array(radar_data[t_radar_col], dtype=float)
        else:
            t_radar_col = find_column(radar_cols, ["Timestamp", "time"])
            if not t_radar_col:
                raise ValueError("radar.csv missing time columns")
            if session_start_unix is None:
                raise ValueError("session_start_unix.txt required when radar lacks Unix_Time")
            t_radar = session_start_unix + np.array(radar_data[t_radar_col], dtype=float)

        bpm_col = find_column(radar_cols, ["Breath_Rate_BPM", "BPM"])
        radar_bpm = np.array(radar_data[bpm_col], dtype=float) if bpm_col else np.full(t_radar.shape, np.nan)

        feature_names = [
            "Peak_To_Noise",
            "PresenceBin_To_Noise",
            "PSD_Peak_Ratio_1_2",
            "Bandpower_6_30_BPM",
            "Motion_RMS",
            "Motion_P2P",
            "Intra_Presence_Score",
            "Inter_Presence_Score",
            "Intra_Over_Inter_Max",
            "Sweep_Energy_STD",
            "Frame_Energy",
        ]
        features = {}
        for name in feature_names:
            if name in radar_data:
                features[name] = np.array(radar_data[name], dtype=float)
            else:
                features[name] = np.full(t_radar.shape, np.nan)

        # build uncertainty_windows.csv
        header = [
            "t_rel_enter_s",
            "phase",
            "belt_bpm",
            "radar_bpm_hat",
            "abs_error",
            "valid_fraction_in_window",
        ] + feature_names + ["feature_coverage_count"]

        rows = []
        for i in range(len(t_k_unix)):
            phase_val = phase[i].get(phase_col, "") if i < len(phase) else ""
            win = aggregate_window(t_k_unix[i], t_radar, features)

            # valid fraction in window
            w_start = t_k_unix[i] - 30.0
            w_end = t_k_unix[i]
            order = np.argsort(t_radar)
            t_sorted = t_radar[order]
            i0 = np.searchsorted(t_sorted, w_start, side="left")
            i1 = np.searchsorted(t_sorted, w_end, side="right")
            bpm_win = radar_bpm[order][i0:i1]
            valid_frac = float(np.sum(np.isfinite(bpm_win)) / bpm_win.size) if bpm_win.size > 0 else 0.0

            row = [
                float(t_k_rel[i]),
                phase_val,
                float(belt_bpm[i]) if np.isfinite(belt_bpm[i]) else "",
                float(radar_bpm_hat[i]) if np.isfinite(radar_bpm_hat[i]) else "",
                float(abs_error[i]) if np.isfinite(abs_error[i]) else "",
                valid_frac,
            ]
            for name in feature_names:
                v = win[name]
                row.append(float(v) if np.isfinite(v) else "")
            row.append(win["feature_coverage_count"])
            rows.append(row)

        uncertainty_path = session_dir / "uncertainty_windows.csv"
        save_csv(uncertainty_path, header, rows)

        # evaluation rows
        phase_list = [r.get(phase_col, "") for r in phase]
        eval_mask = np.array([p == "eval" for p in phase_list])
        eval_abs = abs_error[eval_mask & np.isfinite(abs_error)]
        N_eval = int(eval_abs.size)

        # define bad windows
        is_bad = np.array([False] * len(rows), dtype=bool)
        if N_eval >= 8:
            thresh = float(np.nanpercentile(eval_abs, 75))
            is_bad = (abs_error >= thresh) & eval_mask & np.isfinite(abs_error)
        elif N_eval >= 4:
            thresh = float(np.nanmedian(eval_abs))
            is_bad = (abs_error > thresh) & eval_mask & np.isfinite(abs_error)
        else:
            # keep all false
            pass

        # plots
        figs_dir = session_dir / "figs"
        figs_dir.mkdir(exist_ok=True)

        def scatter_plot(x, y, xlabel, ylabel, title, path):
            plt.figure(figsize=(5, 4))
            plt.scatter(x, y, alpha=0.8)
            plt.xlabel(xlabel)
            plt.ylabel(ylabel)
            plt.title(title)
            plt.tight_layout()
            plt.savefig(path)
            plt.close()

        # scatter plots (eval only)
        eval_idx = eval_mask & np.isfinite(abs_error)
        for feat, fname in [
            ("Peak_To_Noise", "unc_scatter_abs_error_vs_peak_to_noise.png"),
            ("PSD_Peak_Ratio_1_2", "unc_scatter_abs_error_vs_psd_peak_ratio.png"),
            ("Sweep_Energy_STD", "unc_scatter_abs_error_vs_sweep_energy_std.png"),
        ]:
            fcol = header.index(feat)
            feat_vals = np.array([to_float(r[fcol]) for r in rows], dtype=float)
            scatter_plot(
                feat_vals[eval_idx],
                abs_error[eval_idx],
                feat,
                "Abs Error (BPM)",
                f"Abs Error vs {feat}",
                figs_dir / fname,
            )

        # timeseries key features 1hz
        if aligned_1hz_path.exists():
            cols_1hz, data_1hz = read_csv_numeric(aligned_1hz_path)
            t_1hz = np.array(data_1hz[find_column(cols_1hz, ["t_rel_enter_s", "t"])] , dtype=float)
            phase_1hz_col = find_column(cols_1hz, ["phase_1hz", "phase"])
            phase_1hz = []
            with aligned_1hz_path.open("r", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    phase_1hz.append(row.get(phase_1hz_col, ""))

            feats_to_plot = ["Peak_To_Noise", "PSD_Peak_Ratio_1_2", "Sweep_Energy_STD", "Motion_RMS"]
            fig, axes = plt.subplots(len(feats_to_plot), 1, figsize=(12, 7), sharex=True)
            for ax, feat in zip(axes, feats_to_plot):
                if feat in data_1hz:
                    ax.plot(t_1hz, data_1hz[feat], label=feat, linewidth=1)
                    ax.set_ylabel(feat)
                # phase shading
                for phase_name, color in [("pre_enter", "#dddddd"), ("cold_grace", "#ffeecc"), ("eval", "#e6f2ff")]:
                    mask = np.array([p == phase_name for p in phase_1hz])
                    if np.any(mask):
                        idx = np.where(mask)[0]
                        ax.axvspan(t_1hz[idx[0]], t_1hz[idx[-1]], color=color, alpha=0.2)
            axes[-1].set_xlabel("Time since human enter (s)")
            fig.suptitle("Key Radar Features (1Hz, relative to enter)")
            plt.tight_layout()
            plt.savefig(figs_dir / "unc_timeseries_key_features_1hz.png")
            plt.close()

        # boxplots good vs bad
        if N_eval >= 8:
            good_mask = eval_mask & (~is_bad) & np.isfinite(abs_error)
            bad_mask = is_bad & np.isfinite(abs_error)
            feats_for_box = ["Peak_To_Noise", "PSD_Peak_Ratio_1_2", "Sweep_Energy_STD", "Motion_RMS"]
            plt.figure(figsize=(10, 4))
            data = []
            labels = []
            for feat in feats_for_box:
                fcol = header.index(feat)
                feat_vals = np.array([to_float(r[fcol]) for r in rows], dtype=float)
                data.append(feat_vals[good_mask])
                data.append(feat_vals[bad_mask])
                labels.append(f"{feat}\nGood")
                labels.append(f"{feat}\nBad")
            plt.boxplot(data, labels=labels, showfliers=False)
            plt.title("Good vs Bad Windows")
            plt.tight_layout()
            plt.savefig(figs_dir / "unc_good_vs_bad_boxplots.png")
            plt.close()

        # hypothesis report
        report_lines = []
        report_lines.append(f"N_eval points: {N_eval}")

        if N_eval >= 8:
            report_lines.append("Spearman correlations with abs_error (eval only):")
            corr_list = []
            for feat in feature_names:
                fcol = header.index(feat)
                feat_vals = np.array([to_float(r[fcol]) for r in rows], dtype=float)
                corr = spearman_corr(feat_vals[eval_idx], abs_error[eval_idx])
                if np.isfinite(corr):
                    corr_list.append((feat, corr))
            corr_list.sort(key=lambda x: -abs(x[1]))
            for feat, corr in corr_list[:5]:
                report_lines.append(f"- {feat}: {corr:.3f}")
        else:
            report_lines.append("Spearman correlations skipped (N_eval < 8).")

        report_lines.append("")
        report_lines.append("Interpretations (heuristics):")
        report_lines.append("- Low Peak_To_Noise => likely weak signal / noise floor issue")
        report_lines.append("- Low PSD_Peak_Ratio_1_2 or low Bandpower => low spectral evidence / unstable breathing signature")
        report_lines.append("- High Motion_RMS / high Intra/Inter => motion disturbance")
        report_lines.append("- High Sweep_Energy_STD => acquisition instability")
        report_lines.append("")
        report_lines.append("Limitations: short sessions can yield few eval points; correlations may be unstable.")

        (session_dir / "hypothesis_report.md").write_text("\n".join(report_lines))

        print(f"[{session_id}] Wrote {uncertainty_path}")
        print(f"[{session_id}] Wrote {session_dir / 'hypothesis_report.md'}")
        print(f"[{session_id}] Wrote figures in {figs_dir}")


if __name__ == "__main__":
    main()
