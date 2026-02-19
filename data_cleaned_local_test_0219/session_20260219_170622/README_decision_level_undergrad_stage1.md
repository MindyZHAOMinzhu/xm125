# decision_level_undergrad_stage1.csv

## What this file is
`decision_level_undergrad_stage1.csv` is a student-friendly, decision-level table for exploring respiration sensing uncertainty.

- One row = one second (`t_sec`)
- It is derived from frame-level radar data, then aggregated to 1-second features
- Time range is filtered to `t_rel_enter > -20` seconds

This file is designed for exploratory analysis, not for final clinical conclusions.

## Suggested use
Use this table to answer:
1. When does radar become unstable?
2. Which factors are associated with larger radar-vs-belt disagreement?
3. How do motion, signal quality, and presence confidence affect reliability?

## Column glossary

### Time
- `t_sec`: Unix second (integer alignment key)
- `t_rel_enter`: Seconds relative to human-enter time (float)
- `t_rel_enter_sec`: `floor(t_rel_enter)` for simpler plotting

### Reference and radar target
- `belt_rr_raw`: Raw belt breathing rate (sparse updates)
- `belt_rr_ffill`: Forward-filled belt breathing rate (continuous reference)
- `Breath_Rate_BPM_1hz`: Radar breathing rate at 1 Hz decision level

### Radar rate stability (within-second)
- `BPM_median`: Median frame-level BPM in this second
- `BPM_std`: Standard deviation of frame-level BPM
- `BPM_IQR`: Interquartile range of frame-level BPM

### Data availability / reliability
- `n_frames`: Number of radar frames in this second
- `n_valid`: Number of frames with valid breathing rate output
- `valid_ratio`: `n_valid / n_frames`
- `quality_active_ratio`: Fraction of frames with active quality state (`breathing` or `breathing_no_rate`)
- `breathing_valid_ratio`: Fraction of frames where breathing is marked valid
- `quality_true_ratio`: Legacy field; may be empty for final-format sessions

### Presence and context
- `presence_true_ratio`: Fraction of frames with presence detected
- `Presence_Distance_m_mean`: Mean detected distance (m)
- `Presence_Distance_m_std`: Distance variability within the second

### Motion-related uncertainty
- `Motion_RMS_mean`: Mean motion intensity in this second
- `Motion_RMS_std`: Motion variability in this second
- `Motion_RMS_p90`: 90th percentile of motion intensity (captures strong motion tail)
- `motion_active_ratio`: Fraction of frames with motion above a session-derived threshold (P75 of Motion_RMS)

### Spectral clarity
- `PSD_Peak_Height_mean`: Mean spectral peak height
- `PSD_Peak_Ratio_1_2_mean`: Mean primary/secondary spectral peak ratio

## Recommended first analyses
1. Plot `Breath_Rate_BPM_1hz` and `belt_rr_ffill` over `t_rel_enter_sec`
2. Compute absolute error: `abs(Breath_Rate_BPM_1hz - belt_rr_ffill)`
3. Correlate error with `valid_ratio`, `BPM_std`, `motion_active_ratio`, `quality_active_ratio`
4. Compare low-motion vs high-motion segments using `motion_active_ratio`

## Important cautions
- `belt_rr_raw` is sparse by design; missing values are expected.
- Keep `t_rel_enter` (float) for accurate timing; use `t_rel_enter_sec` only for readability.
- This file is for uncertainty exploration and hypothesis generation.
