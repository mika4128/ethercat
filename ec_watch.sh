#!/bin/sh
# ec_watch.sh — T153 EtherCAT 事件监视 (被动, 只读 dmesg)
# 新事件追加 /home/mika/ec_watch.log; >1MB 轮转保留尾部 500KB
LOG=/home/mika/ec_watch.log
last=0
while :; do
  new=$(dmesg 2>/dev/null | grep -a "EtherCAT" | awk -F'[][]' -v L="$last" '$2+0 > L {print}')
  if [ -n "$new" ]; then
    { date +"%F %T"; echo "$new"; } >> "$LOG"
    last=$(dmesg 2>/dev/null | grep -a "EtherCAT" | tail -1 | awk -F'[][]' '{print $2+0}')
    sz=$(wc -c < "$LOG" 2>/dev/null || echo 0)
    if [ "$sz" -gt 1000000 ]; then
      tail -c 500000 "$LOG" > "$LOG.1" && mv "$LOG.1" "$LOG"
    fi
  fi
  sleep 30
done
