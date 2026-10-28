#!/usr/bin/env bash
# Start every demo process from one terminal. No tmux needed.
#
#   scripts/start-all.sh                 start everything; logs go to .logs/<name>.log
#   scripts/start-all.sh --skip worker   everything except the Python worker; run
#                                        `make worker` in its own terminal so you can
#                                        kill it during the worker-crash scenario
#   scripts/start-all.sh --follow        also stream every log here, prefixed by name
#   scripts/start-all.sh --no-doctor     skip the preflight check at the end
#   UI_PORT=5180 scripts/start-all.sh    serve the console on another port
#
# Ctrl-C (or closing the terminal) stops everything this script started. A service
# of ours that is already running is left alone; a port held by something else is
# reported with the process holding it. Works with macOS's bash 3.2.

set -u
SCRIPT="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$SCRIPT")/.."

SERVICES="temporal mcp java worker api ui"
SKIP=" "
FOLLOW=0
DOCTOR=1
PIDS=""
NAMES=""
STOPPING=0
BLOCKED_LIST=""

usage() { sed -n '2,15p' "$SCRIPT" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --skip)
      [ $# -ge 2 ] || { echo "--skip needs a name, one of: $SERVICES"; exit 2; }
      case " $SERVICES " in *" $2 "*) ;; *) echo "Unknown service '$2'. Use one of: $SERVICES"; exit 2 ;; esac
      SKIP="$SKIP$2 "
      shift 2
      ;;
    --follow) FOLLOW=1; shift ;;
    --no-doctor) DOCTOR=0; shift ;;
    -h | --help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 2 ;;
  esac
done

skipped() { case "$SKIP" in *" $1 "*) return 0 ;; esac; return 1; }

# A setting from the environment, or else from .env (without surrounding quotes).
env_value() {
  eval "v=\${$1:-}"
  if [ -z "$v" ] && [ -f .env ]; then
    v=$(sed -n "s/^$1=//p" .env | tail -n 1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/")
  fi
  printf '%s' "$v"
}

# If TEMPORAL_ADDRESS points at a self-hosted service or Temporal Cloud, don't start a local dev server.
TEMPORAL_TARGET=$(env_value TEMPORAL_ADDRESS)
REMOTE_TEMPORAL=0
case "${TEMPORAL_TARGET:-localhost:7233}" in
  localhost:* | 127.0.0.1:* | "[::1]:"* | localhost | 127.0.0.1) ;;
  *) REMOTE_TEMPORAL=1; SKIP="${SKIP}temporal " ;;
esac
TEMPORAL_UI=$(env_value TEMPORAL_UI_URL)
TEMPORAL_UI=${TEMPORAL_UI:-http://localhost:8233}
UI_PORT="${UI_PORT:-5173}"
export UI_PORT
port_of() { case "$1" in temporal) echo 7233 ;; mcp) echo 8765 ;; java) echo 8081 ;; api) echo 8000 ;; ui) echo "$UI_PORT" ;; esac; }
limit_of() { case "$1" in java) echo 300 ;; *) echo 90 ;; esac; } # first Maven run downloads dependencies
needs() { case "$1" in temporal) echo temporal ;; mcp | worker | api) echo uv ;; java) echo mvn ;; ui) echo npm ;; esac; }
is_open() { (exec 3<>"/dev/tcp/localhost/$1") >/dev/null 2>&1; }

# An open port isn't proof that it's ours: ask each service something only it answers.
is_ours() {
  case "$1" in
    temporal) temporal operator cluster health --address "localhost:$2" 2>/dev/null | grep -q SERVING; return $? ;;
    ui) path=/ want="Incident copilot" ;;
    api) path=/api/config want="scenarios" ;;
    mcp) path=/healthz want="tools" ;;
    java) path=/health want="change-management" ;;
    *) return 0 ;;
  esac
  command -v curl >/dev/null 2>&1 || return 0 # can't tell without curl; trust the port
  curl -s -m 3 "http://localhost:$2$path" 2>/dev/null | grep -q "$want"
}

port_owner() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | awk 'NR > 1 { printf "            PID %s  %s\n", $2, $1 }' | sort -u
  else
    echo "            (find it with: lsof -nP -iTCP:$1 -sTCP:LISTEN)"
  fi
}
pid_of() { for e in $NAMES; do case "$e" in "$1":*) echo "${e#*:}"; return ;; esac; done; }
say() { printf '  %-9s %s\n' "$1" "$2"; }

cleanup() {
  [ "$STOPPING" = 1 ] && return
  STOPPING=1
  [ -z "$PIDS" ] && return
  echo
  echo "Stopping everything this script started..."
  for pid in $PIDS; do kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null; done
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    alive=0
    for pid in $PIDS; do kill -0 "$pid" 2>/dev/null && alive=1; done
    [ "$alive" = 0 ] && break
    sleep 0.5
  done
  for pid in $PIDS; do kill -KILL -- "-$pid" 2>/dev/null; done
  echo "Stopped."
}
trap 'cleanup; exit 130' INT TERM
trap 'cleanup; exit 129' HUP
trap cleanup EXIT

# ---------------------------------------------------------------- preflight --

missing=""
command -v make >/dev/null 2>&1 || missing="$missing make"
for name in $SERVICES; do
  skipped "$name" && continue
  [ "$name" = temporal ] && [ "$REMOTE_TEMPORAL" = 1 ] && continue
  tool=$(needs "$name")
  command -v "$tool" >/dev/null 2>&1 || case " $missing " in *" $tool "*) ;; *) missing="$missing $tool" ;; esac
done
if [ -n "$missing" ]; then
  echo "Missing tools:$missing"
  echo "Install them (see README prerequisites), or skip a service with --skip <name>."
  exit 1
fi
if [ ! -f .env ]; then
  echo "No .env found. Run 'make setup' first (it creates .env from .env.example)."
  exit 1
fi
if ! skipped ui && [ ! -d ui/node_modules ]; then
  echo "UI dependencies aren't installed. Run 'make setup' (or 'make setup-ui') first."
  exit 1
fi

# -------------------------------------------------------------------- start --

start() {
  name=$1
  if [ "$name" = temporal ] && [ "$REMOTE_TEMPORAL" = 1 ]; then
    say remote "Temporal at $TEMPORAL_TARGET (TEMPORAL_ADDRESS); not starting a local dev server"
    return
  fi
  if skipped "$name"; then say skip "$name (start it yourself: make $name)"; return; fi
  port=$(port_of "$name")
  if [ -n "$port" ] && is_open "$port"; then
    if is_ours "$name" "$port"; then
      say running "$name is already running on :$port; leaving it alone"
    else
      say BLOCKED "port $port is in use by another program, so $name can't start:"
      port_owner "$port"
      if [ "$name" = ui ]; then
        echo "            Stop it (kill <PID>) and rerun, or use another port: UI_PORT=5180 make up"
      else
        echo "            Stop it (kill <PID>) and rerun."
      fi
      BLOCKED_LIST="$BLOCKED_LIST $name"
    fi
    return
  fi
  log=".logs/$name.log"
  : >"$log"
  # Job control only while forking: the service gets its own process group (so it can
  # be stopped as a unit, children included), while Ctrl-C still reaches this script.
  set -m
  if [ "$FOLLOW" = 1 ]; then
    prefix=$(printf '%-8s| ' "$name")
    (make --no-print-directory "$name" 2>&1 | tee -a "$log" | awk -v p="$prefix" '{ print p $0; fflush() }') </dev/null &
  else
    (make --no-print-directory "$name" >>"$log" 2>&1) </dev/null &
  fi
  pid=$!
  set +m
  PIDS="$PIDS $pid"
  NAMES="$NAMES $name:$pid"
  say starting "$name (log: $log)"
}

wait_ready() {
  name=$1
  skipped "$name" && return 0
  pid=$(pid_of "$name")
  [ -z "$pid" ] && return 0 # already running before we started
  port=$(port_of "$name")
  if [ -z "$port" ]; then
    # No port to probe (the worker): give it a moment, then make sure it's alive.
    sleep 4
    if kill -0 "$pid" 2>/dev/null; then say ready "$name (process running)"; return 0; fi
    say FAILED "$name exited. Last lines of .logs/$name.log:"
    tail -n 15 ".logs/$name.log" | sed 's/^/            /'
    return 1
  fi
  limit=$(limit_of "$name")
  waited=0
  while ! is_open "$port"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      say FAILED "$name exited. Last lines of .logs/$name.log:"
      tail -n 15 ".logs/$name.log" | sed 's/^/            /'
      return 1
    fi
    if [ "$waited" -ge "$limit" ]; then
      say TIMEOUT "$name isn't listening on :$port after ${limit}s; see .logs/$name.log"
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
  say ready "$name on :$port (${waited}s)"
}

mkdir -p .logs
echo "Starting the incident copilot demo"
start temporal
case " $BLOCKED_LIST " in *" temporal "*) echo "Temporal's port is taken, so nothing else can run."; exit 1 ;; esac
wait_ready temporal || { echo "Temporal didn't start, so nothing else can run."; exit 1; }

for name in mcp java worker api ui; do start "$name"; done

failed=""
for name in mcp worker api ui java; do wait_ready "$name" || failed="$failed $name"; done

if [ "$DOCTOR" = 1 ]; then
  echo
  echo "Preflight check:"
  make --no-print-directory doctor 2>&1 | sed 's/^/  /'
fi

echo
case " $BLOCKED_LIST $failed " in
  *" ui "*) echo "Console:      not running (see above)" ;;
  *) echo "Console:      http://localhost:$UI_PORT" ;;
esac
echo "Temporal UI:  $TEMPORAL_UI"
echo "Logs:         .logs/<name>.log   (for example: tail -f .logs/worker.log)"
[ -n "$failed" ] && echo "Not running:  $failed  (check their logs, then start them with make <name>)"
[ -n "$BLOCKED_LIST" ] && echo "Blocked:     $BLOCKED_LIST  (port held by another program; see above)"
if skipped worker; then echo "Worker:       start it in another terminal with: make worker"; fi
echo
echo "Running. Press Ctrl-C to stop everything this script started."

dead=" "
while true; do
  sleep 3
  for e in $NAMES; do
    name=${e%%:*}
    pid=${e#*:}
    if ! kill -0 "$pid" 2>/dev/null; then
      case "$dead" in *" $name "*) ;; *)
        dead="$dead$name "
        echo "  [$(date +%H:%M:%S)] $name stopped. Restart it in another terminal with: make $name  (log: .logs/$name.log)"
        ;;
      esac
    fi
  done
done
