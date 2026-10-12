#!/bin/zsh
# Offline check (SPEC 4.6, 7): run the app's scripted UI flow with the real model
# and sample every internet socket on this Mac twice a second. Fails if any socket
# belongs to the app or its worker, or if a WebKit helper opens a socket that was
# not open before the run (WebKit helpers are shared, so they are compared to a baseline).
# Run: harness/offline_check.sh   (quit Safari and other WebKit apps first for a clean baseline)
set -u
cd "${0:A:h}/.."
LSOF=/usr/sbin/lsof
# The monitor must work, or silence would read as "no sockets": prove it sees this shell's files.
[[ -x $LSOF ]] && $LSOF -w -nP -p $$ 2>/dev/null | grep -q . || { echo "OFFLINE CHECK: FAIL (lsof does not work)"; exit 1; }
log=$(mktemp -t ldoffline)
err=$(mktemp -t ldofflineerr)
home=$(mktemp -d -t ldhome)
webkit() { $LSOF -w +c 0 -nP -i 2>>"$err" | awk '$1 ~ /WebKit/ {print $1, $2, $5, $8, $9}' | sort -u; }
base=$(webkit)  # before the app starts, so none of its sockets can hide in the baseline
LOCALDEID_HOME=$home LOCALDEID_TEST_KEYSTORE=1 PYTHONPATH=src .venv/bin/python -m localdeid.app --smoke \
  --out "$home/shots" > "$log.smoke" 2>&1 &
app=$!
samples=0
descendants() {  # the app and every process below it (not the harness's own process group)
  local todo=($1) all=($1) p kids
  while (( ${#todo} )); do
    p=${todo[1]}; todo=(${todo[2,-1]})
    kids=($(pgrep -P $p))
    todo+=($kids); all+=($kids)
  done
  print -r -- ${(j:,:)all}
}
while kill -0 $app 2>/dev/null; do
  pids=$(descendants $app)
  $LSOF -w -nP -i -a -p "$pids" 2>>"$err" | tail -n +2 >> "$log"
  comm -13 <(print -r -- "$base") <(webkit) | sed 's/^/new WebKit socket: /' >> "$log"
  samples=$((samples + 1))
  sleep 0.2
done
wait $app; rc=$?
echo "smoke exit: $rc · samples: $samples · internet sockets seen: $(wc -l < "$log" | tr -d ' ')"
if [[ -s $log ]]; then sort -u "$log"; fi
if [[ -s $err ]]; then echo "lsof errors:"; sort -u "$err" | head -5; fi
ok=0; [[ $rc -eq 0 && ! -s $log && ! -s $err && $samples -gt 4 ]] && ok=1
rm -rf "$home" "$log" "$log.smoke" "$err"
[[ $ok -eq 1 ]] && echo "OFFLINE CHECK: PASS" || { echo "OFFLINE CHECK: FAIL"; exit 1; }
