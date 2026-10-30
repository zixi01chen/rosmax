#!/bin/bash
# rosmax 实时压测面板：N 组独立 pub/sub 节点并行压测，每秒刷新实时性能表。
# 用法: live_stress.sh [组数] [频率Hz] [payload字节]   （Ctrl-C 停止并清理）
# 默认: 3 组 × 200Hz × 1KB，UDP，domain 77
N=${1:-3}
RATE=${2:-200}
SIZE=${3:-1024}

source /opt/ros/jazzy/setup.bash 2>/dev/null
export AMENT_PREFIX_PATH=/root/rosmax_overlay:$AMENT_PREFIX_PATH
export LD_LIBRARY_PATH=/root/rosmax_overlay/lib:/opt/ros/jazzy/lib:$LD_LIBRARY_PATH
export ROS_DOMAIN_ID=77
export FASTRTPS_DEFAULT_PROFILES_FILE=/root/rosmax_overlay/fastdds_udp_only.xml
export LC_ALL=C LANG=C
BIN=/root/rosmax_overlay/lib/rosmax_nodes

mkdir -p /root/rosmax_live && cd /root/rosmax_live
rm -f sub_*.log pub_*.log

PIDS=()
for i in $(seq 0 $((N-1))); do
  $BIN/test_subscriber --ros-args -p topic_name:=/rosmax/live_$i \
      -p qos_profile:=reliable_volatile_depth10 -p ignore_sec:=2.0 > sub_$i.log 2>&1 &
  PIDS+=($!)
done
sleep 3
for i in $(seq 0 $((N-1))); do
  $BIN/test_publisher --ros-args -p topic_name:=/rosmax/live_$i \
      -p qos_profile:=reliable_volatile_depth10 \
      -p payload_size:=$SIZE -p publish_rate_hz:=${RATE}.0 > /dev/null 2>&1 &
  PIDS+=($!)
done

cleanup() {
  for p in "${PIDS[@]}"; do kill $p 2>/dev/null; done
  echo; echo "[$(date +%H:%M:%S)] 已停止并清理 $N 组节点"
  exit 0
}
trap cleanup INT TERM

while true; do
  echo -e "\033[H\033[2J"
  echo "════════ rosmax 实时压测 ════════ $N 组 × ${SIZE}B @ ${RATE}Hz · UDP · domain77"
  echo "════════ $(date +%H:%M:%S) ════════（Ctrl-C 停止）"
  printf "%-8s %10s %8s %10s %10s %10s %8s\n" "topic" "recv" "rate" "p50(ns)" "p99(ns)" "max(ns)" "loss"
  for i in $(seq 0 $((N-1))); do
    LINE=$(grep STATS sub_$i.log 2>/dev/null | tail -1 | sed "s/.*\[STATS\] //")
    if [ -n "$LINE" ]; then
      echo "$LINE" | awk -v t="live_$i" '{
        for(k=1;k<=NF;k++){split($k,v,"=");m[v[1]]=v[2]}
        printf "%-8s %10s %8s %10s %10s %10s %8s\n", t, m["recv"], m["rate"], m["p50"], m["p99"], m["max"], m["loss"]
      }'
    else
      printf "%-8s %10s\n" "live_$i" "启动中..."
    fi
  done
  UPT=$(uptime | sed "s/.*load average/load/")
  TEMP=$(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | head -1 | awk "{printf \"%.1f°C\", \$1/1000}")
  echo "──────────────────────────────────────────────"
  echo "板负载 $UPT · 温度 $TEMP · governor $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null)"
  sleep 1
done
