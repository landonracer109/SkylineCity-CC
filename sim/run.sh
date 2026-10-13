#!/bin/sh
# Runs the fleet sim against a real job server, from the repo root (inside a container with python3,
# luajit and curl, e.g. python:3.12-alpine + "apk add luajit curl"):
#   sh sim/run.sh <turtles> [restarts] [seed] [hours]      (needs tools' export in ./jobs)
cd "$(dirname "$0")/.."
command -v luajit >/dev/null || apk add -q luajit curl >/dev/null
pkill -f citysrv.py; sleep 1
rm -rf /tmp/citydata; mkdir -p /tmp/citydata
DATA_DIR=/tmp/citydata JOBS_DIR=$PWD/jobs CITY_KEY=simkey PORT=8097 python3 -u server/citysrv.py > /tmp/citysrv.log 2>&1 &
sleep 2; trap "pkill -f citysrv.py" EXIT
CITY_KEY=simkey SERVER=http://127.0.0.1:8097/city luajit sim/fleetsim.lua jobs "$@"
