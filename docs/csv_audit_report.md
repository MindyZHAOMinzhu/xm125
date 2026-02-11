# CSV Audit Report (`xm125_breathing_refapp_pi.py`)

## Scope
- File reviewed: `/Users/zhaoxiaozhao/xm125/xm125_breathing_refapp_pi.py`
- Review focus: CSV logging only (header, per-column provenance, blank conditions, type/semantic risks)
- No code changes in this task

## 1) CSV Header (Exact Order)
Header is written at `/Users/zhaoxiaozhao/xm125/xm125_breathing_refapp_pi.py:201`.

Exact column order (41 columns):
1. `Timestamp`
2. `Unix_Time`
3. `Quality_Flag`
4. `Breath_Rate_BPM`
5. `App_State`
6. `Distances_Being_Analyzed`
7. `Presence_Detected`
8. `Presence_Distance_m`
9. `Intra_Presence_Score`
10. `Inter_Presence_Score`
11. `Presence_Distance_Index`
12. `Radar_Enter_Time`
13. `Intra_Max_All`
14. `Inter_Max_All`
15. `Intra_Over_Inter_Max`
16. `Signal_Peak_Bin`
17. `Signal_Peak_Value`
18. `Noise_Median`
19. `Peak_To_Noise`
20. `Signal_At_PresenceBin`
21. `Noise_At_PresenceBin`
22. `PresenceBin_To_Noise`
23. `FastSlow_Diff_Max`
24. `FastSlow_Diff_AtPresenceBin`
25. `Frame_Energy`
26. `Sweep_Energy_STD`
27. `Sweep_Energy_P2P`
28. `Bin_Energy_STD`
29. `PSD_Peak_Idx`
30. `PSD_Peak_Freq_Hz`
31. `PSD_Peak_BPM`
32. `PSD_Peak_Height`
33. `PSD_Peak_Ratio_1_2`
34. `Bandpower_6_30_BPM`
35. `Motion_RMS`
36. `Motion_P2P`
37. `Rate_Hist_Last`
38. `Rate_Hist_Valid_Frac_10s`
39. `Buffer_Coverage_s`
40. `DBA_Start_Idx`
41. `DBA_End_Idx`

Audit result:
- No duplicated column names.
- Header order and row value order are consistent (`row` at line 520 aligns with header at line 201).

## 2) Per-column Provenance Table
| Column | Source object / field | Units + expected type | Blank when | Pitfalls / misinterpretation | Quick sanity check |
|---|---|---|---|---|---|
| `Timestamp` | `current_time = time.time() - session_start_unix` | seconds, float | never | Depends on `session_start_unix.txt`; can start non-zero if reused file | Starts near 0 and increases monotonically |
| `Unix_Time` | `time.time()` | unix seconds, float | never | Wall clock jumps possible if system time changes | Around current epoch (~1.7e9+) |
| `Quality_Flag` | derived from `breathing_result`/`presence_result` availability | string (`breathing`/`breathing_no_rate`/`presence_only`/`none`) | never | `breathing_no_rate` can persist during warm-up; not error by itself | Early frames often `presence_only` or `breathing_no_rate` |
| `Breath_Rate_BPM` | `processed_data.breathing_result.breathing_rate` (`* ratio`, ratio=1) | bpm, float | breathing missing or rate None/NaN | Blank does not mean no subject; may be warm-up | During stable breathing should become ~6-30+ |
| `App_State` | `getattr(processed_data, "app_state", "")` | enum/object string-ish | if missing attribute | Logged raw object, not normalized string; CSV may show enum repr | Should show a small set of repeated state values |
| `Distances_Being_Analyzed` | `_format_distances(processed_data.distances_being_analyzed)` | string | if None | Tuple `(0,3)` is converted to `0.0000;3.0000`, semantic loss | Usually two numbers when in distance-estimation/breathing states |
| `Presence_Detected` | `presence_result.presence_detected` via `_safe_bool` | bool | `presence_result` None | Blank means result missing, not False | Mostly True when subject present in range |
| `Presence_Distance_m` | `presence_result.presence_distance` via `_safe_float` | meters, float | result missing or NaN | Not an index; physical distance estimate | Around configured range `0.4-0.7` m for target setup |
| `Intra_Presence_Score` | `presence_result.intra_presence_score` | score, float | result missing/NaN | High values may indicate fast motion/noise | Should vary with motion |
| `Inter_Presence_Score` | `presence_result.inter_presence_score` | score, float | result missing/NaN | Low inter does not always mean no presence | Should vary slower than intra |
| `Presence_Distance_Index` | `presence_result.extra_result.presence_distance_index` | bin index, int | `extra_result` missing | Can be non-empty while `presence_distance_m` blank in edge cases | Usually small integer in distance-bin range |
| `Radar_Enter_Time` | latched `current_time` when in-range streak `>= enter_k` | seconds, float | never satisfied enter condition | Latches once and never resets within run | Should become fixed scalar after first stable presence |
| `Intra_Max_All` | `max(presence_result.intra)` | score, float | `intra` missing/non-numeric | Can spike on transient noise | Non-negative; often tracks motion bursts |
| `Inter_Max_All` | `max(presence_result.inter)` | score, float | `inter` missing/non-numeric | Low in static scenes | Non-negative |
| `Intra_Over_Inter_Max` | `Intra_Max_All / (Inter_Max_All + 1e-9)` | ratio, float | either max blank | Ratio can explode when `Inter_Max_All` near zero | Larger during fast movement |
| `Signal_Peak_Bin` | `argmax(extra_result.abs_mean_sweep)` | bin index, int | `abs_mean_sweep` missing | Index only meaningful with distance grid context | Usually integer 0..N-1 |
| `Signal_Peak_Value` | `max(extra_result.abs_mean_sweep)` | amplitude proxy, float | same as above | Scale depends on processing chain, not absolute units | Should be positive |
| `Noise_Median` | `median(extra_result.lp_noise)` | noise proxy, float | `lp_noise` missing | Not absolute SNR denominator unless comparable scaling | Positive, relatively stable |
| `Peak_To_Noise` | `Signal_Peak_Value / (Noise_Median + 1e-9)` | ratio, float | signal/noise blank | Inflates if noise ~0 | Typically >1 in good signal |
| `Signal_At_PresenceBin` | `abs_mean_sweep[presence_distance_index]` | amplitude proxy, float | invalid/missing index or array | Depends on valid `Presence_Distance_Index` | Should be close to peak when lock is good |
| `Noise_At_PresenceBin` | `lp_noise[presence_distance_index]` | noise proxy, float | invalid/missing index or array | Can be noisy frame to frame | Positive |
| `PresenceBin_To_Noise` | `Signal_At_PresenceBin / (Noise_At_PresenceBin + 1e-9)` | ratio, float | either term blank | Inflates if local noise tiny | Usually >1 when target locked |
| `FastSlow_Diff_Max` | `max(abs(fast_lp_mean_sweep - slow_lp_mean_sweep))` | delta proxy, float | fast/slow arrays missing or mismatched | Sensitive to abrupt motion | Non-negative |
| `FastSlow_Diff_AtPresenceBin` | `abs(fast_lp[idx]-slow_lp[idx])` | delta proxy, float | invalid idx or arrays missing | Blank if idx unavailable even if diff exists | Rises with local changes at tracked bin |
| `Frame_Energy` | `mean(abs(frame)^2)` from `extra_result.frame` | energy proxy, float | frame missing/non-2D | Not calibrated physical power | Positive and smooth-ish |
| `Sweep_Energy_STD` | `std(mean(abs(frame)^2, axis=1))` | energy spread, float | frame missing/non-2D | High values may indicate instability/jitter | Non-negative |
| `Sweep_Energy_P2P` | `max(sweep_energy)-min(sweep_energy)` | energy spread, float | frame missing/non-2D | Sensitive to outliers | Non-negative |
| `Bin_Energy_STD` | `std(mean(abs(frame)^2, axis=0))` | across-bin spread, float | frame missing/non-2D | Scene-dependent; clutter increases spread | Non-negative |
| `PSD_Peak_Idx` | `argmax(breathing_extra.psd)` | spectrum index, int | breathing extra missing or psd/freq invalid | Index alone has no meaning without freq axis | 0..len(psd)-1 |
| `PSD_Peak_Freq_Hz` | `frequencies[PSD_Peak_Idx]` | Hz, float | same as above | Includes non-breathing peaks if noisy | Breathing peak often ~0.1-0.5 Hz |
| `PSD_Peak_BPM` | `PSD_Peak_Freq_Hz * 60` | bpm, float | same as above | May differ from `Breath_Rate_BPM` during transients | Often near estimated breathing rate |
| `PSD_Peak_Height` | `psd[PSD_Peak_Idx]` | PSD amplitude, float | same as above | Relative metric only | Positive |
| `PSD_Peak_Ratio_1_2` | top1/top2 from psd via `argpartition` | ratio, float | psd size <2 or invalid arrays | Ratio unstable in flat/noisy spectra | Higher is cleaner dominant peak |
| `Bandpower_6_30_BPM` | `sum(psd[(f>=0.1)&(f<=0.5)])` | PSD bandpower, float | psd/freq missing | Not normalized by bin width; compare relatively | Positive; rises with breathing signal strength |
| `Motion_RMS` | `sqrt(mean(breathing_motion^2))` | motion proxy, float | `breathing_motion` missing | Amplitude proxy only; not displacement in meters | Positive |
| `Motion_P2P` | `max(breathing_motion)-min(breathing_motion)` | motion proxy, float | `breathing_motion` missing | Outlier-sensitive | Positive |
| `Rate_Hist_Last` | last finite of `breathing_rate_history` | bpm (expected), float | history missing or all NaN | Can lag current frame estimate | Should become finite after warm-up |
| `Rate_Hist_Valid_Frac_10s` | finite fraction in last 10s window using `time_vector` mask | fraction [0,1], float | vector/history missing, mismatched, or empty mask | Depends on proper `time_vector` monotonicity | Near 1 in stable tracking |
| `Buffer_Coverage_s` | `time_vector[-1]-time_vector[0]` | seconds, float | missing/too-short time_vector | Buffer length may vary with implementation | Usually near history duration window |
| `DBA_Start_Idx` | from tuple `processed_data.distances_being_analyzed[0]` | bin index, int | distances not tuple len=2 | Blank when distances logged as list/None | Integer start of analyzed range |
| `DBA_End_Idx` | from tuple `processed_data.distances_being_analyzed[1]` | bin index, int | same as above | Inclusive/exclusive interpretation unspecified in CSV | Integer end of analyzed range |

## 3) Frame Gating / State Gating Analysis

### `breathing_result` gating
- Source: `/Users/zhaoxiaozhao/xm125/xm125_breathing_refapp_pi.py:427`
- If `breathing_result is None`:
  - `Breath_Rate_BPM` stays blank.
  - All breathing-derived columns (`PSD_*`, `Bandpower_*`, `Motion_*`, `Rate_Hist_*`, `Buffer_Coverage_s`) stay blank.
  - `Quality_Flag` becomes `presence_only` if presence exists, otherwise `none`.
- If `breathing_result` exists but `breathing_rate` is `None`/NaN:
  - `Quality_Flag = breathing_no_rate`
  - `Breath_Rate_BPM` remains blank.

### `presence_result` gating
- Source: `/Users/zhaoxiaozhao/xm125/xm125_breathing_refapp_pi.py:300`
- If `presence_result is None`:
  - Presence base columns and presence-derived scalar columns remain blank.
  - `Quality_Flag` may still become `breathing` / `breathing_no_rate` if breathing exists.
- If `presence_result` exists but `extra_result` missing:
  - `Presence_Distance_Index` and many proxy columns remain blank.

### `Radar_Enter_Time` logic
- Source: `/Users/zhaoxiaozhao/xm125/xm125_breathing_refapp_pi.py:310`
- Set once when all true:
  - `presence_detected is True`
  - `presence_distance` is numeric
  - `enter_min <= presence_distance <= enter_max`
  - condition is met for `enter_k` consecutive frames
- Remains blank forever if above never satisfied.
- Never resets during same run (latched behavior).

## 4) Likely Issues / Mismatches

1. `App_State` type consistency risk
- Current write is raw object: `getattr(processed_data, "app_state", "")`.
- Depending on SDK object `__str__/__repr__`, CSV may contain enum name or verbose object repr.
- Risk: downstream grouping may be unstable across SDK versions.

2. `Distances_Being_Analyzed` semantic compression
- `_format_distances` converts tuple/list/array to semicolon-separated float string.
- A tuple like `(0, 3)` becomes `0.0000;3.0000`, losing explicit tuple semantics.
- Mitigation currently exists via appended `DBA_Start_Idx` and `DBA_End_Idx`.

3. `Presence_Distance_m` unit check
- In code it comes from `presence_result.presence_distance`; this is meter distance (not index).
- Index is logged separately as `Presence_Distance_Index`.

4. Columns computed but never updated
- No CSV column appears to be declared but never assigned.
- Non-CSV variable `frame_idx` increments but is not logged (not a bug for CSV correctness, just unused for output).

5. CSV writer / flush integrity
- `open(..., newline="")` is correct for cross-platform CSV line endings.
- Periodic flush + `os.fsync` every `flush_every_n` frames is present.
- Final file close via context manager is correct.

## 5) Summary: Coverage vs Needs

### Current coverage by category
- Timing: `Timestamp`, `Unix_Time`
- Breathing output: `Breath_Rate_BPM`, `Quality_Flag`
- Presence output: `Presence_Detected`, `Presence_Distance_m`, `Intra/Inter_Presence_Score`, `Presence_Distance_Index`
- Distance selection: `Distances_Being_Analyzed`, `DBA_Start_Idx`, `DBA_End_Idx`
- Signal/noise proxy: `Signal_*`, `Noise_*`, `Peak_To_Noise`, `PresenceBin_To_Noise`, `FastSlow_*`, `Frame_Energy` family
- Motion proxy: `Motion_RMS`, `Motion_P2P`, `Rate_Hist_*`, `Buffer_Coverage_s`
- Engineering/loop health: limited to `Quality_Flag` transitions and latched `Radar_Enter_Time` (no explicit loop latency/state dwell metrics)

### Missing categories (based on schema and your debug goals)
- Explicit engineering loop health metrics are missing:
  - no `Loop_Dt_s`
  - no per-state dwell timing (`State_Dwell_s`)
  - no `Since_Enter_s`
- Breathing validity explicit boolean missing (currently infer from blank/non-blank `Breath_Rate_BPM`).
- Traceability metadata missing (script path/config/git hash).
- Uses `breathing_rate_history` but not `all_breathing_rate_history`.

## Note: Verifying Executed Script Path
Potential old-file confusion is possible because session launcher runs via relative path:
- `/Users/zhaoxiaozhao/xm125/run_session.sh:47` calls `../xm125_breathing_refapp_pi.py` from session directory.

Minimal verification steps (no code change):
1. Add temporary `pwd` and `ls -l ../xm125_breathing_refapp_pi.py` before launch in shell.
2. Check process command line while running: `ps -ef | rg xm125_breathing_refapp_pi.py`.
3. Compare file mtime/hash before run: `ls -l` and `sha256sum`.

## Minimal next additions (not implemented)
Top 10 scalar columns to add later, highest value for engineering + uncertainty prep:
1. `Loop_Dt_s`
2. `State_Dwell_s`
3. `Since_Enter_s`
4. `Breathing_Valid`
5. `Breath_Rate_Hz`
6. `Distance_Bin_Center_m`
7. `Intra_Max_InSlice`
8. `Inter_Max_InSlice`
9. `Script_Path`
10. `Run_Config_JSON`

