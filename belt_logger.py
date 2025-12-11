import time
import csv
import argparse
from datetime import datetime
from pathlib import Path
import sys          # ⭐ 新增：为了返回退出码给 shell

from gdx import gdx


def record_belt_breathing_rate(
    csv_filename="belt_breathing_log.csv",
    duration_s=300,
    sample_interval_s=1,
):
    # 先尝试读取 session_start_unix.txt（和 run_session.sh 对齐）
    session_start_path = Path("session_start_unix.txt")
    if session_start_path.exists():
        session_start_unix = float(session_start_path.read_text().strip())
        print(f"Using session_start_unix from file: {session_start_unix}")
    else:
        session_start_unix = time.time()
        print(f"No session_start_unix.txt, fallback to {session_start_unix}")

    g = gdx.gdx()
    print("Opening GDX-RB...")

    try:
        # USB 连接（你的设备是 GDX-RB）
        g.open(connection="usb")  # 或 g.open_usb()，看 gdx 版本
        print("GDX-RB opened successfully.")
    except Exception as e:
        print(f"Error opening GDX: {e}")
        # ⭐ 打开失败 → 直接返回非 0，供 shell 检测
        return 1

    # 通道说明：一般 2 是呼吸率 BPM
    g.select_sensors([2])  # 1-Force; 2-respiratory rate - bpm

    # period 单位是毫秒，这里 1000 = 1 Hz
    g.start(period=1000)

    # 用 start_time 控制采集时长 & 采样间隔
    start_time = time.time()

    # ⭐ 新增：用于“没数据就退出”的逻辑
    got_any_data = False                    # 开始到现在有没有拿到过 data
    NO_DATA_TIMEOUT = 8                     # 比如 8 秒一直没有 data 就判失败

    with open(csv_filename, "w", newline="") as f:
        writer = csv.writer(f)
        # ✅ 对齐 radar 风格的列名
        writer.writerow(
            [
                "Timestamp",              # 相对时间（相对于 session_start_unix）
                "Unix_Time",              # 绝对 unix 秒（浮点）
                "Time_HMS",               # HH:MM:SS
                "Belt_Breath_Rate_BPM",   # 呼吸率
                "Is_New_Value",           # 是否和上一帧 bpm 不同
            ]
        )

        last_bpm = None
        print("Starting data collection...")

        k = 0
        try:
            while time.time() - start_time < duration_s:
                now_unix = time.time()
                elapsed = now_unix - session_start_unix      # ✅ 用 session 起点
                now_unix_rounded = int(now_unix)
                human_time = datetime.fromtimestamp(
                    now_unix_rounded
                ).strftime("%H:%M:%S")

                data = g.read()

                if data is not None:
                    got_any_data = True          # ⭐ 至少读到过一次数据
                    bpm = data[0]
                    is_new = bpm != last_bpm
                    last_bpm = bpm

                    writer.writerow(
                        [
                            f"{elapsed:.3f}",       # Timestamp (sec)
                            now_unix,               # Unix_Time（保留浮点）
                            human_time,             # Time_HMS
                            f"{bpm:.2f}",           # Belt_Breath_Rate_BPM
                            is_new,                 # Is_New_Value
                        ]
                    )
                    f.flush()
                else:
                    print(f"{human_time}: No data returned")

                # ⭐ 关键：如果从开始到现在都没拿到任何 data，且超过 NO_DATA_TIMEOUT 秒 → 判失败
                if (not got_any_data) and (time.time() - start_time > NO_DATA_TIMEOUT):
                    print(f"❌ No belt data for {NO_DATA_TIMEOUT} seconds after start. Exiting with error.")
                    try:
                        g.stop()
                    except Exception as e:
                        print(f"stop() error: {e}")
                    try:
                        g.close()
                    except Exception as e:
                        print(f"close() error: {e}")
                    # 返回非 0：给 run_session.sh 检测用
                    return 2

                k += 1
                # 按 sample_interval_s 控制 read 频率
                next_tick = start_time + k * sample_interval_s
                time.sleep(max(0, next_tick - time.time()))

        except KeyboardInterrupt:
            print("Data collection stopped by user.")
        finally:
            print("Stopping and closing GDX...")
            try:
                g.stop()
            except Exception as e:
                print(f"stop() error: {e}")
            try:
                g.close()
            except Exception as e:
                print(f"close() error: {e}")
            print(f"Saved data to {csv_filename}")

    # ⭐ 正常结束：返回 0
    return 0


def parse_args():
    parser = argparse.ArgumentParser(
        description="Record GDX-RB breathing rate to CSV"
    )
    parser.add_argument(
        "--out",
        "-o",
        dest="csv_filename",
        default=None,
        help="Output CSV filename (e.g., belt_log.csv)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    # automatic naming
    if args.csv_filename is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.csv_filename = f"belt_log_{ts}.csv"

    exit_code = record_belt_breathing_rate(
        csv_filename=args.csv_filename,
        duration_s=300,
        sample_interval_s=1,
    )
    # ⭐ 把函数返回码透传给 shell
    sys.exit(exit_code)
