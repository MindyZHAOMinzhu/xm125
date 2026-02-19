# CSV Audit Report (`xm125_breathing_refapp_pi.py`)

## Scope
- File reviewed: `/Users/zhaoxiaozhao/xm125/xm125_breathing_refapp_pi.py`
- Focus: CSV logging semantics, semantic overlap, and column meaning completeness
- Status: Updated to match current code after adding 6 minimal columns

## Current Header (Exact Order)
Header is defined at `/Users/zhaoxiaozhao/xm125/xm125_breathing_refapp_pi.py:220`.

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
42. `Timestamp_Unix_ms`
43. `Frame_Idx`
44. `Breath_Valid`
45. `Inter_Frame_Dt_ms`
46. `Sweeps_Per_Frame`
47. `Resp_Waveform_Value`

Audit result:
- No duplicated names.
- Header and row write order are consistent (`row` starts at line 577).

## Semantic Overlap Review (Not Duplicates)
These pairs/groups are close in topic but not semantically duplicate:

1. `Timestamp` vs `Unix_Time` vs `Timestamp_Unix_ms`
- `Timestamp`: relative seconds since `session_start_unix`.
- `Unix_Time`: absolute epoch seconds.
- `Timestamp_Unix_ms`: absolute epoch milliseconds for cross-device alignment.

2. `Quality_Flag` vs `Breath_Valid`
- `Quality_Flag`: categorical lifecycle/status (`breathing`, `breathing_no_rate`, `presence_only`, `none`).
- `Breath_Valid`: strict bool for filtering/metrics.

3. `Motion_RMS` / `Motion_P2P` / `Resp_Waveform_Value`
- `Motion_RMS`: window-level energy proxy.
- `Motion_P2P`: window-level amplitude span.
- `Resp_Waveform_Value`: instantaneous sample (latest buffer value).

4. `Distances_Being_Analyzed` vs `DBA_Start_Idx` / `DBA_End_Idx`
- String-form combined field plus parsed explicit bounds.

5. `Breath_Rate_BPM` vs `PSD_Peak_BPM` vs `Rate_Hist_Last`
- Current estimator output, current dominant spectral peak, and history tail value.

Conclusion:
- No hard duplicates.
- Current overlaps are useful for joining, filtering, and diagnostics.

## Per-column Meaning (Grouped)

### Timing and indexing
| Column | Meaning | Type / Unit | Blank when |
|---|---|---|---|
| `Timestamp` | Relative time since session start | float / s | never |
| `Unix_Time` | Absolute wall-clock time | float / s | never |
| `Timestamp_Unix_ms` | Absolute wall-clock time for alignment | int / ms | never |
| `Frame_Idx` | Frame counter incremented per written row | int | never |
| `Inter_Frame_Dt_ms` | Inter-frame interval from local timestamps | float / ms | first frame |

### Breathing status/output
| Column | Meaning | Type / Unit | Blank when |
|---|---|---|---|
| `Quality_Flag` | Output availability/status class | string | never |
| `Breath_Rate_BPM` | Breathing rate estimate | float / bpm | no valid rate |
| `Breath_Valid` | Whether `Breath_Rate_BPM` is numeric this frame | bool | never |
| `Rate_Hist_Last` | Last finite value in breathing rate history buffer | float / bpm | no finite history |
| `Rate_Hist_Valid_Frac_10s` | Finite fraction in last 10s history window | float [0,1] | missing/mismatch buffers |
| `Buffer_Coverage_s` | `time_vector[-1]-time_vector[0]` | float / s | missing short time vector |

### Presence output
| Column | Meaning | Type / Unit | Blank when |
|---|---|---|---|
| `Presence_Detected` | Presence detector boolean | bool | `presence_result` missing |
| `Presence_Distance_m` | Presence distance estimate | float / m | missing/NaN |
| `Intra_Presence_Score` | Intra-frame motion score | float | missing/NaN |
| `Inter_Presence_Score` | Inter-frame motion score | float | missing/NaN |
| `Presence_Distance_Index` | Presence distance bin index | int | extra_result missing |
| `Radar_Enter_Time` | First latched in-range presence time | float / s | enter condition never satisfied |

### Distance selection
| Column | Meaning | Type / Unit | Blank when |
|---|---|---|---|
| `App_State` | RefApp state object/string representation | enum/string | missing attribute |
| `Distances_Being_Analyzed` | Analyzed distance bins/range, stringified | string | missing |
| `DBA_Start_Idx` | Parsed start bin if tuple | int | not tuple |
| `DBA_End_Idx` | Parsed end bin if tuple | int | not tuple |

### Presence scalar evidence and signal/noise
| Column | Meaning | Type / Unit | Blank when |
|---|---|---|---|
| `Intra_Max_All` | Max of presence intra curve | float | intra missing |
| `Inter_Max_All` | Max of presence inter curve | float | inter missing |
| `Intra_Over_Inter_Max` | `Intra_Max_All/(Inter_Max_All+eps)` | float | either missing |
| `Signal_Peak_Bin` | Argmax of `abs_mean_sweep` | int | abs_mean_sweep missing |
| `Signal_Peak_Value` | Max of `abs_mean_sweep` | float | abs_mean_sweep missing |
| `Noise_Median` | Median of `lp_noise` | float | lp_noise missing |
| `Peak_To_Noise` | `Signal_Peak_Value/(Noise_Median+eps)` | float | signal/noise missing |
| `Signal_At_PresenceBin` | Signal at presence bin | float | invalid/missing index |
| `Noise_At_PresenceBin` | Noise at presence bin | float | invalid/missing index |
| `PresenceBin_To_Noise` | `Signal_At_PresenceBin/(Noise_At_PresenceBin+eps)` | float | either missing |
| `FastSlow_Diff_Max` | Max abs delta of fast vs slow LP sweep | float | fast/slow missing |
| `FastSlow_Diff_AtPresenceBin` | Fast-slow abs delta at presence bin | float | invalid index or missing |

### Acquisition integrity
| Column | Meaning | Type / Unit | Blank when |
|---|---|---|---|
| `Frame_Energy` | Mean of `abs(frame)^2` | float | frame missing/non-2D |
| `Sweep_Energy_STD` | Std across sweep energies | float | frame missing/non-2D |
| `Sweep_Energy_P2P` | Peak-to-peak across sweep energies | float | frame missing/non-2D |
| `Bin_Energy_STD` | Std across bin energies | float | frame missing/non-2D |

### Spectral and motion proxies
| Column | Meaning | Type / Unit | Blank when |
|---|---|---|---|
| `PSD_Peak_Idx` | Index of max PSD bin | int | psd/freq missing |
| `PSD_Peak_Freq_Hz` | Frequency at PSD peak | float / Hz | psd/freq missing |
| `PSD_Peak_BPM` | PSD peak converted to BPM | float / bpm | psd/freq missing |
| `PSD_Peak_Height` | Max PSD value | float | psd/freq missing |
| `PSD_Peak_Ratio_1_2` | Top1/top2 PSD ratio | float | fewer than 2 bins or missing |
| `Bandpower_6_30_BPM` | Sum PSD in 0.1..0.5 Hz band | float | psd/freq missing |
| `Motion_RMS` | RMS of breathing_motion | float | breathing_motion missing |
| `Motion_P2P` | Peak-to-peak of breathing_motion | float | breathing_motion missing |
| `Resp_Waveform_Value` | Latest breathing_motion sample | float | breathing_motion missing |
| `Sweeps_Per_Frame` | Configured sweeps per frame | int | never |

## Gating Behavior Summary
- If `presence_result` is missing, all presence and presence-extra derived columns remain blank.
- If `breathing_result` is missing, all breathing-extra derived columns remain blank, and `Quality_Flag` is `presence_only`/`none`.
- If `breathing_result` exists but rate is missing/NaN, `Breath_Rate_BPM` blank and `Breath_Valid=False`.
- `Radar_Enter_Time` is latched once and remains constant afterward.

## Practical Interpretation Pitfalls
1. `App_State` is currently logged raw, may vary by SDK representation.
2. `Distances_Being_Analyzed` is stringified; for index math prefer `DBA_Start_Idx/DBA_End_Idx`.
3. `Peak_To_Noise` and `PresenceBin_To_Noise` are linear ratios, not dB.
4. `Inter_Frame_Dt_ms` is host-side timing proxy, not guaranteed sensor-side sweep timing.

## Minimal Next Additions (Not Implemented)
Top 10 recommended future columns if you继续加强工程诊断（仍保持轻量）：
1. `Loop_Dt_s`
2. `State_Dwell_s`
3. `Since_Enter_s`
4. `Breath_Rate_Hz`
5. `Distance_Bin_Center_m`
6. `Intra_Max_InSlice`
7. `Inter_Max_InSlice`
8. `Script_Path`
9. `Run_Config_JSON`
10. `Git_Commit`

## Path Verification Note
To avoid running old script by accident, current launcher uses relative path from session dir:
- `/Users/zhaoxiaozhao/xm125/run_session.sh:47`

Quick checks:
1. `pwd` and `ls -l ../xm125_breathing_refapp_pi.py` before start.
2. `ps -ef | rg xm125_breathing_refapp_pi.py` while running.
3. `sha256sum /Users/zhaoxiaozhao/xm125/xm125_breathing_refapp_pi.py` before run.
