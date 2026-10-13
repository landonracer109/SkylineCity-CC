#!/bin/sh
# Runs several fleet sims at once (one job server each), for a PC with many cores. From the repo
# root, in Git Bash or Linux:
#   PY=<python> JOBS=<exported jobs dir> sh sim/run-many.sh
# Each line of SCENARIOS is: name seed restarts env-settings...  Results go to sim-out/<name>.txt.
cd "$(dirname "$0")/.."
PY=${PY:-python3}
JOBS=${JOBS:-jobs}
TURTLES=${TURTLES:-16}
STAMP=$(date +%H%M%S)
SCENARIOS=${SCENARIOS:-"plain 31 4
pause 32 2 PAUSE=7200,9000
stockout 33 2 STOCKOUT=928:6,2522:0,159:14 STOCKBACK=3
both 34 4 PAUSE=3600,5400 STOCKOUT=928:6,2522:0 STOCKBACK=2"}
mkdir -p sim-out
port=8110
while read -r name seed restarts envs; do
  [ -z "$name" ] && continue
  port=$((port + 1))
  data="sim-out/data-$name-$STAMP"; mkdir -p "$data"
  (
    DATA_DIR="$data" JOBS_DIR="$JOBS" CITY_KEY=simkey PORT=$port "$PY" -I server/citysrv.py > "sim-out/$name-server.log" 2>&1 &
    srv=$!
    sleep 3
    env $envs CITY_KEY=simkey SERVER="http://127.0.0.1:$port/city" SIMDIR="sim-out/fleet-$name-$STAMP" LOGLINES=0 \
      luajit sim/fleetsim.lua "$JOBS" "$TURTLES" "$restarts" "$seed" 16 > "sim-out/$name.txt" 2>&1
    kill $srv 2>/dev/null
    echo "$name done: $(grep -E '^blocks ' "sim-out/$name.txt")"
  ) &
done <<EOF
$SCENARIOS
EOF
wait
