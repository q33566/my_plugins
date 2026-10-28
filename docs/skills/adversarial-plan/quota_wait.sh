#!/bin/bash
# Exit when the usage guard has stopped work (5-hour usage >= its threshold) AND
# that window has reset — i.e. when a run the guard stopped can resume. Reads the
# same files, with the same defaults, as the guard (usage-guard/scripts/guard.sh).
DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
CONF="$DIR/usage-guard.json"; LIMITS="$DIR/usage-limits.json"
command -v jq >/dev/null || { echo "quota_wait: jq not found — cannot watch the usage guard"; exit 2; }
[ -f "$LIMITS" ] || { echo "quota_wait: $LIMITS not found — is CLAUDE_CONFIG_DIR the dir the guard uses?"; exit 2; }

settings() {  # prints "<enabled> <threshold>" exactly as the guard would read them
  if [ -f "$CONF" ]; then
    jq -r '"\(if .enabled == null then true else .enabled end) \(.threshold // 70)"' "$CONF" 2>/dev/null \
      || echo "invalid 70"
  else
    echo "true 70"
  fi
}
window() {  # prints "<used%> <resets_at epoch seconds>" or nothing
  jq -r 'def epoch: if . > 100000000000 then . / 1000 else . end | floor;
         def ts: if type == "number" then epoch
                 elif type == "string" then
                   (tonumber? | epoch) //
                   (capture("^(?<d>[0-9-]+T[0-9:]+)(\\.[0-9]+)?(?<z>Z|[+-][0-9]{2}:?[0-9]{2})?$")
                    | (.d + "Z" | fromdateiso8601) -
                      (if (.z // "Z") == "Z" then 0
                       else (.z | sub(":"; "")) as $o
                            | ($o[0:1]) as $s | (($o[1:3] | tonumber) * 3600 + ($o[3:5] | tonumber) * 60)
                            | if $s == "+" then . else -. end end))
                   // null
                 else null end;
         .five_hour | select(. != null) | "\(.used_percentage // "") \(.resets_at | ts // "")"' "$LIMITS" 2>/dev/null
}

read -r en th <<<"$(settings)"
echo "quota_wait: watching $LIMITS (guard enabled=$en, threshold=${th}%)"
[ "$en" = "invalid" ] && echo "quota_wait: $CONF is unreadable — the guard ignores it too, so it will not stop the run"
tripped=""
while true; do
  now=$(date +%s)
  read -r en th <<<"$(settings)"
  read -r p r <<<"$(window)"
  if [ -z "$tripped" ] && [ "$en" = "true" ] && [ -n "$p" ] && [ -n "$r" ] && [ "$r" -gt "$now" ] \
     && awk -v p="$p" -v t="$th" 'BEGIN{exit !(p>=t)}'; then
    tripped=$r
    echo "quota_wait: guard tripped at ${p}% (threshold ${th}%); waiting until the window resets at $(date -r "$r" '+%H:%M' 2>/dev/null || date -d "@$r" '+%H:%M')"
  fi
  if [ -n "$tripped" ] && [ "$now" -gt $((tripped + ${QUOTA_MARGIN:-90})) ]; then
    echo "QUOTA RESTORED: the 5-hour window has reset — resume the interrupted adversarial-plan or adversarial-build run (and re-arm this watcher)"
    exit 0
  fi
  sleep "${QUOTA_POLL:-60}"
done
