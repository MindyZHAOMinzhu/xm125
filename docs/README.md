# XM125 Breathing RefApp Debug Pipeline

## Purpose and Design Goals
This repo contains a **two-stage pipeline** for debugging and validating the XM125 A121 Breathing RefApp before doing uncertainty analysis.

Primary goals:
- **Engineering debug first**: catch IO/loop timing issues, state-machine stalls, and misconfigurations that produce bad sessions.
- **Sensing uncertainty later**: once the pipeline is stable, use the same logs to compute uncertainty metrics (U_spec / U_temp / U_ens, etc.).

In short: **stabilize the pipeline, then quantify uncertainty**.

## Pipeline Diagram (Text)
```
XM125 RefApp (xm125_breathing_refapp_pi.py)
  -> H5 recording + CSV (per frame, diagnostic columns)
  -> analyze_sessions.py
  -> session_summary.csv + trial_summary.csv + report.md + plots/
```

## Quickstart
### 1) Run the RefApp logger
```
python /Users/zhaoxiaozhao/xm125/xm125_breathing_refapp_pi.py \
  --port /dev/ttyUSB0 \
  --prefix my_session
```
Outputs:
- `my_session_radar.h5`
- `my_session_radar.csv`

### 2) Analyze one or many sessions
```
python /Users/zhaoxiaozhao/xm125/analyze_sessions.py \
  --input_dir /path/to/sessions \
  --output_dir /path/to/output
```
Outputs:
- `session_summary.csv`
- `trial_summary.csv`
- `report.md`
- `plots/` (histograms, scatters, time series)

## Where Files Go
- Session CSV/H5 are written next to where you run the logger (or by your `--prefix`).
- Analysis outputs go in the specified `--output_dir`.

## What to Read Next
- `docs/refapp_logging.md` — detailed logging semantics and CSV schema.
- `docs/analyze_debug.md` — analysis, flags, plots, and interpretation.
