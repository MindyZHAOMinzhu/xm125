#!/bin/bash

set -e

trap '
echo "Stopping all sensors...";
kill $RADAR_PID $BELT_PID 2>/dev/null || true;
sleep 0.2;
exit 1
' INT


SESSION_ID=$(date +%Y%m%d_%H%M%S)
SESSION_DIR="session_${SESSION_ID}"
mkdir -p "$SESSION_DIR"
cd "$SESSION_DIR"

# 0) Record Session start unix
SESSION_START_UNIX=$(date +%s)
echo "$SESSION_START_UNIX" > session_start_unix.txt
echo "📁 Session folder created: $SESSION_DIR"
echo

######################################
# 1) Start Radar
######################################
echo "Starting radar..."
/home/mindy/xm125_env/bin/python ../xm125_breathing_refapp_pi.py \
    --prefix "${SESSION_ID}" &
RADAR_PID=$!
echo "✨ Radar PID = $RADAR_PID"
echo

######################################
# 2) Start Belt
######################################
echo "Starting belt..."
sudo /home/mindy/xm125_env/bin/python ../belt_logger.py \
    --out "${SESSION_ID}_belt.csv" &
BELT_PID=$!
echo "✨ Belt PID = $BELT_PID"
echo

######################################
# 3) Human enter time marker
######################################
echo "👉 When subject sits at position, press Enter..."
read

HUMAN_ENTER_UNIX=$(date +%s)
echo "$HUMAN_ENTER_UNIX" > human_enter_time.txt
echo "📌 Logged human enter time: $HUMAN_ENTER_UNIX"
echo

######################################
# 4) Check belt availability  
######################################
echo "Checking belt status..."
sleep 10

# If belt process has already exited, check its exit code
if ! kill -0 "$BELT_PID" 2>/dev/null; then
    echo "⚠️ Belt process has exited, checking exit code..."
    wait "$BELT_PID"
    BELT_STATUS=$?

    if [ "$BELT_STATUS" -ne 0 ]; then
        echo "❌ Belt failed (exit code $BELT_STATUS) — aborting session."
        kill "$RADAR_PID" 2>/dev/null || true
        cd ..
        rm -rf "$SESSION_DIR"
        exit 1
    else
        echo "ℹ️ Belt exited normally (exit code 0)."
    fi
else
    echo "✅ Belt OK and running!"
fi

echo "Collecting... Press Ctrl+C to stop."

wait
