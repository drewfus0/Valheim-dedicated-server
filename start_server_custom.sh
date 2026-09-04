#!/bin/bash
export templdpath=$LD_LIBRARY_PATH
export LD_LIBRARY_PATH=./linux64:$LD_LIBRARY_PATH
export SteamAppId=892970

echo "Starting server PRESS CTRL-C to exit"

GAME_LOG="valheim_server.log"
PERF_LOG="valheim_performance.log"

# 1. Start Server
./valheim_server.x86_64 -name "My server" -port 2456 -world "Dedicated" -password "secret" > "$GAME_LOG" 2>&1 &
VALHEIM_PID=$!

# 2. Start Web Server on port 8080
python3 -m http.server 8080 > /dev/null 2>&1 &
WEB_PID=$!

echo "=== Logger Started: $(date) (PID: $VALHEIM_PID) ===" >> "$PERF_LOG"

(
    while kill -0 $VALHEIM_PID 2>/dev/null; do
        PLAYER_COUNT=0
        PLAYER_LIST=""

        if [ -f "$GAME_LOG" ]; then
            CONNS=$(grep "Peer " "$GAME_LOG" | grep -c "connected")
            DISCONNS=$(grep -c "Destroying peer" "$GAME_LOG")
            PLAYER_COUNT=$((CONNS - DISCONNS))
            if [ $PLAYER_COUNT -lt 0 ]; then PLAYER_COUNT=0; fi

            if [ $PLAYER_COUNT -gt 0 ]; then
                NAMES=$(grep "Got character ZDOID from" "$GAME_LOG" | grep -v " : 0:0" | awk -F'from ' '{print $2}' | awk -F' :' '{print $1}' | tail -n "$PLAYER_COUNT" | tr '\n' ',' | sed 's/,$//')
                PLAYER_LIST="[${NAMES}]"
            else
                PLAYER_LIST="[]"
            fi
        fi

        STATS=$(ps -p $VALHEIM_PID -o %cpu=,%mem= 2>/dev/null)
        if [ -z "$STATS" ]; then break; fi

        CPU=$(echo $STATS | awk '{print $1}')
        MEM=$(echo $STATS | awk '{print $2}')
        TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

        # Write to Log
        echo "$TIMESTAMP | CPU: ${CPU}% | MEM: ${MEM}% | Players: ${PLAYER_COUNT} | Online: ${PLAYER_LIST}" >> "$PERF_LOG"

        # Generate HTML Dashboard
        cat <<EOF > index.html
<!DOCTYPE html>
<html>
<head>
    <title>Server Status</title>
    <meta http-equiv="refresh" content="10">
    <style>body { font-family: monospace; padding: 2rem; background: #121212; color: #eee; }</style>
</head>
<body>
    <h2>Valheim Server Status</h2>
    <p><strong>Updated:</strong> $TIMESTAMP</p>
    <p><strong>CPU:</strong> ${CPU}% | <strong>MEM:</strong> ${MEM}%</p>
    <p><strong>Players (${PLAYER_COUNT}):</strong> ${PLAYER_LIST}</p>
</body>
</html>
EOF

        sleep 10
    done
    echo "=== Logger Stopped: $(date) ===" >> "$PERF_LOG"
) &

# 3. Wait for Valheim to close, then kill the web server
wait $VALHEIM_PID
kill $WEB_PID 2>/dev/null

export LD_LIBRARY_PATH=$templdpath
