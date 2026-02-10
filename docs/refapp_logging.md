# xm125_breathing_refapp_pi.py — RefApp Logging Details

This document explains how the **XM125 A121 Breathing RefApp logger** works and what every CSV column means.

## 1) Script Responsibility and Workflow

### Session start time
- Reads `session_start_unix.txt` (if present) to define a **global session time origin**.
- If missing, falls back to `time.time()` when the script starts.
- The CSV `Timestamp` column is **seconds since `session_start_unix`**.

### Serial connection
- Connects to XM125 via serial (`--port`, default `/dev/ttyUSB0`).
- Uses A121 client with 115200 baud.

### RefAppConfig and Presence config
- Creates `BreathingProcessorConfig` and `PresenceProcessorConfig`.
- Creates `RefAppConfig` with:
  - range (`start_m`, `end_m`)
  - number of distances to analyze
  - distance determination duration
  - profile and sweeps per frame

These parameters drive **which distance bins are analyzed** and the **stability vs responsiveness** of the algorithm.

### Logging strategy
- Uses `a121.H5Recorder` to capture raw session data.
- Writes one **CSV row per frame**.
- Uses **throttled console prints** (`--print-every-s`) to avoid IO slowdowns.
- Flushes CSV every N rows (`--flush-every-n`) for safety.

---

## 2) CSV Schema Reference (All Columns)

Each column below has:
- **Type**: float/int/bool/string
- **Units**: seconds, meters, bpm, Hz, etc.
- **Source**: Raw API / Derived / Metadata
- **Computation**: how it is calculated
- **Blank when**: conditions that produce empty values
- **Pitfalls**: common interpretation mistakes

### Timing & Indexing
| Column | Type | Units | Source | Computation | Blank When | Pitfalls |
|---|---|---|---|---|---|---|
| Frame_Idx | int | count | Derived | Incremented per loop | Never | Not time-based; gaps possible if frames dropped |
| Timestamp | float | s | Derived | `Unix_Time - session_start_unix` | Never | Not wall time; tied to session start |
| Unix_Time | float | s | Derived | `time.time()` | Never | Wall clock; subject to system time changes |

### Engineering Health Metrics
| Column | Type | Units | Source | Computation | Blank When | Pitfalls |
|---|---|---|---|---|---|---|
| Loop_Dt_s | float | s | Derived | `perf_counter` around `get_next` + processing | Never | Includes any local processing time (not just sensor) |
| State_Dwell_s | float | s | Derived | Time in current `App_State` | On first row | Resets whenever state changes |
| Since_Enter_s | float | s | Derived | `Timestamp - Radar_Enter_Time` | If enter not set | Only meaningful after enter event |

### Breathing Outputs
| Column | Type | Units | Source | Computation | Blank When | Pitfalls |
|---|---|---|---|---|---|---|
| Quality_Flag | string | - | Derived | `breathing` / `breathing_no_rate` / `presence_only` / `none` | Never | Indicates availability, not quality |
| Breath_Rate_BPM | float | bpm | Raw API | `breathing_result.breathing_rate` | If not available / NaN | This is the RefApp estimate, not ground truth |
| Breath_Rate_Hz | float | Hz | Derived | `Breath_Rate_BPM / 60` | If `Breathing_Valid` false | Not a spectral peak; just converted BPM |
| Breathing_Valid | bool | - | Derived | True if `Breath_Rate_BPM` is valid float | Never | “Valid” means present, not accurate |

### Presence Outputs
| Column | Type | Units | Source | Computation | Blank When | Pitfalls |
|---|---|---|---|---|---|---|
| Presence_Detected | bool | - | Raw API | `presence_result.presence_detected` | If no presence result | Presence = motion, not semantic human |
| Presence_Distance_m | float | m | Raw API | `presence_result.presence_distance` | If no presence result | Motion location, not body center |
| Intra_Presence_Score | float | - | Raw API | `presence_result.intra_presence_score` | If no presence result | High intra can be motion artifacts |
| Inter_Presence_Score | float | - | Raw API | `presence_result.inter_presence_score` | If no presence result | Inter reflects slower changes |
| Presence_Distance_Index | int | index | Raw API | `presence_result.extra_result.presence_distance_index` | If extra_result missing | Not meters; index into distance grid |

### Distance Selection / Distances_Being_Analyzed
| Column | Type | Units | Source | Computation | Blank When | Pitfalls |
|---|---|---|---|---|---|---|
| Distances_Being_Analyzed | string | index or m | Raw API | `processed_data.distances_being_analyzed` formatted | If missing | Often a tuple `(start_idx, end_idx)` |
| DBA_Start_Idx | int | index | Derived | Parsed from `Distances_Being_Analyzed` if tuple | If not tuple | Used for slicing presence curves |
| DBA_End_Idx | int | index | Derived | Parsed from `Distances_Being_Analyzed` if tuple | If not tuple | End index interpretation depends on range grid |
| Distance_Bin_Center_m | float | m | Derived | Center index mapped to meters if grid available | If grid missing | Only valid if distance grid is known |

### Presence Curve Scalar Features (Derived)
These compress the full **intra/inter curves** into compact scalars.

| Column | Type | Units | Source | Computation | Blank When | Pitfalls |
|---|---|---|---|---|---|---|
| Intra_Max_All | float | - | Derived | `max(intra)` over full curve | If intra missing | Sensitive to outliers |
| Inter_Max_All | float | - | Derived | `max(inter)` over full curve | If inter missing | Sensitive to outliers |
| Intra_Max_InSlice | float | - | Derived | `max(intra)` within DBA slice | If slice missing | Depends on correct slice interpretation |
| Inter_Max_InSlice | float | - | Derived | `max(inter)` within DBA slice | If slice missing | Same as above |
| Intra_Over_Inter | float | - | Derived | `intra_presence_score / (inter_presence_score + 1e-6)` (fallback to slice max ratio) | If missing inputs | High ratio often indicates fast motion |

### Breathing PSD Scalar Features (Derived)
These are derived from `breathing_result.extra_result.psd` + `frequencies`.

| Column | Type | Units | Source | Computation | Blank When | Pitfalls |
|---|---|---|---|---|---|---|
| PSD_Peak_Freq_Hz | float | Hz | Derived | freq at max PSD | If PSD missing | Only as good as RefApp PSD output |
| PSD_Peak_BPM | float | bpm | Derived | `PSD_Peak_Freq_Hz * 60` | If PSD missing | Not ground truth; spectral peak |
| PSD_Peak_Height | float | - | Derived | max PSD | If PSD missing | Relative, not absolute |
| PSD_Peak_Ratio_1_2 | float | - | Derived | peak1 / peak2 | If <2 peaks | Low ratio indicates ambiguity |
| Bandpower_6_30_BPM | float | - | Derived | sum PSD between 6–30 bpm (0.1–0.5 Hz) | If PSD missing | Only meaningful if freq spacing valid |

### Radar Enter Time / Alignment
| Column | Type | Units | Source | Computation | Blank When | Pitfalls |
|---|---|---|---|---|---|---|
| Radar_Enter_Time | float | s | Derived | Enter time when presence in target range for K frames | If never triggered | Depends on thresholds and distance accuracy |

### Run Metadata
| Column | Type | Units | Source | Computation | Blank When | Pitfalls |
|---|---|---|---|---|---|---|
| Script_Path | string | - | Metadata | `os.path.abspath(__file__)` | Never | Not meaningful if script moved |
| Run_Config_JSON | string | - | Metadata | JSON dump of key config params | Never | Must parse JSON for fields |
| Git_Commit | string | - | Metadata | `git rev-parse --short HEAD` | If not in git repo | Empty in deployed systems |

---

## 3) Semantics Notes (Critical)

- **Presence_Detected = motion threshold trigger**, not semantic human presence.
- **App_State** is the algorithm state, not a user/behavior state.
- **Distances_Being_Analyzed** is often a **slice of distance indices**, not actual meters.
- **Distance_Bin_Center_m** only exists if the distance grid is available from the SDK.
- **PSD-derived features are evidence proxies**, not accuracy or certainty.

---

## 4) Troubleshooting Guide

### Symptoms likely caused by code/IO/loop timing
- `Loop_Dt_s` p95 or max spikes
- Inconsistent `Frame_Idx` / missing rows
- Frequent or excessive console prints

**Action:** reduce prints, increase `--print-every-s`, reduce heavy computations, ensure USB/serial stability.

### Symptoms likely caused by state-machine stalls
- Long `State_Dwell_s` in `NO_PRESENCE_DETECTED` or `DETERMINE_DISTANCE_ESTIMATE`
- `Presence_Detected` oscillates or never goes true

**Action:** check range (`start_m/end_m`), presence thresholds, and environment motion noise.

### Symptoms likely caused by distance instability
- High `distance_std`
- `Distance_Bin_Center_m` or `Presence_Distance_m` jumps

**Action:** verify mounting stability, environment clutter, and target placement.

### Symptoms likely caused by weak spectral evidence
- Low `PSD_Peak_Height`
- Low `PSD_Peak_Ratio_1_2`
- Low `Bandpower_6_30_BPM`

**Action:** ensure adequate motion amplitude and stable subject position.
