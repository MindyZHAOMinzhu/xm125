# analyze_sessions.py — Debug/Validation Analysis

This document explains how the offline analysis script works and how to interpret its outputs.

## 1) What the Analysis Script Does

### Inputs
- A directory containing one or more session CSV files.
- Each CSV is expected to include base and diagnostic columns (see `docs/refapp_logging.md`).

### Trial segmentation
Default behavior:
- **One trial per file**.
- If `Radar_Enter_Time` exists, trial is `[Radar_Enter_Time, Radar_Enter_Time + 60s]`.
- If `Radar_Enter_Time` is missing, use the **whole file**.

Optional:
- `--multi_trial` treats each unique `Radar_Enter_Time` as a separate trial window.

### Summary metrics computed
Per session (and per trial):
- `valid_fraction`: mean of `Breathing_Valid`.
- `bpm_median`, `bpm_mad`: median and MAD of `Breath_Rate_BPM` on valid frames.
- `time_to_first_bpm`: first `Since_Enter_s` where breathing is valid.
- `distance_std`: std of `Distance_Bin_Center_m` (fallback: `Presence_Distance_m`).
- `intra_over_inter_median`: median of `Intra_Over_Inter` (fallback to max-slice ratio).
- `presence_fraction`: mean of `Presence_Detected`.
- State occupancy: fraction of time in each `App_State`.
- Loop timing: `loop_p95` and `loop_max` from `Loop_Dt_s`.

If PSD features exist:
- `psd_peak_bpm_median`, `psd_peak_bpm_mad`
- `psd_peak_ratio_median`
- `bandpower_6_30_bpm_median`

### Flagging rules (configurable thresholds)
Default bad-session flags:
- `valid_fraction < 0.3`
- `time_to_first_bpm > 20s`
- `loop_p95 > 0.2s`
- `loop_max > 0.5s`
- `max_state_dwell > 15s` in `NO_PRESENCE_DETECTED` or `DETERMINE_DISTANCE_ESTIMATE`

All thresholds are configurable via CLI args.

---

## 2) Outputs and Interpretation

### session_summary.csv
One row per session. Key columns:
- Core quality: `valid_fraction`, `bpm_median`, `bpm_mad`, `time_to_first_bpm`
- Stability: `distance_std`, `intra_over_inter_median`
- IO timing: `loop_p95`, `loop_max`
- State info: `max_state_dwell`, `state_frac_<STATE>`
- Flags: `bad_session`, plus specific `flag_*` columns
- Optional PSD summaries if present

### trial_summary.csv
One row per trial window. Same concept as session summary, but per-trial.

### report.md
Includes:
- **Top flagged sessions** and reasons
- **Correlation table** for hypothesis validation

### plots/
- Loop timing histogram (all sessions)
- Stacked bar chart of App_State fractions
- Scatter plots for hypotheses (valid_fraction vs distance_std, intra_over_inter, loop_p95)
- Time-series plots for the worst sessions (breath rate, distance, intra/inter scores)

### Why correlations may be NaN
Correlation requires **multiple sessions**. With only one session, all correlations are undefined.

---

## 3) Hypotheses / Validation Logic

The script is designed to test these hypotheses:

**H1: Bad sessions have lower valid_fraction and longer time_to_first_bpm.**
- Evidence: high `flag_valid`, high `flag_ttfb`, low `valid_fraction`.

**H2: Bad sessions have higher intra_over_inter (fast motion) and/or higher intra maxima.**
- Evidence: elevated `intra_over_inter_median` in flagged sessions.

**H3: Bad sessions have more distance instability.**
- Evidence: higher `distance_std` in flagged sessions.

**H4: Bad sessions show weaker spectral evidence.**
- Evidence: low `psd_peak_height`, low `psd_peak_ratio_median`, low `bandpower_6_30_bpm_median`.

**H5: Engineering/IO issues show up as slow loop timing and missing breathing.**
- Evidence: high `loop_p95`/`loop_max` with low `valid_fraction`.

Correlation plots and the correlation table in `report.md` are used to evaluate these hypotheses.

---

## 4) Extension Roadmap (Short)

### Why this prepares for uncertainty metrics
This pipeline ensures **data integrity and algorithm stability** before adding uncertainty metrics (U_spec, U_temp, U_ens). These metrics require:
- Stable loop timing
- Consistent state-machine operation
- Reliable distance selection
- Repeatable spectral evidence

### Recommended next steps
1. Run multiple sessions per configuration.
2. Sweep key parameters (distance range, presence thresholds, time_series_length_s).
3. Compare distributions across sessions (not just single-session behavior).
4. Once stable, add uncertainty metrics based on session-to-session variance.
