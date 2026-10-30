#!/bin/bash
# 等 .20 板恢复并自动执行 W1.1c 最小闭环（pub/sub 上板出 STATS）。
# 用法: bash scripts/wait_board.sh   （Ctrl-C 可中断；板重启后运行即可）
BOARD=10.112.133.20
echo "[$(date +%H:%M:%S)] 等待 $BOARD SSH 恢复（每 60s 探测一次，Ctrl-C 退出）..."
until timeout 25 sshpass -p root ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 \
      root@$BOARD 'echo OK' 2>/dev/null | grep -q OK; do
  echo "[$(date +%H:%M:%S)] 仍不可达，继续等..."
  sleep 60
done
echo "[$(date +%H:%M:%S)] 板恢复！执行 W1.1c 最小闭环..."

sshpass -p root ssh -o StrictHostKeyChecking=no root@$BOARD bash <<'EOF' 2>&1 | tail -20
source /opt/ros/jazzy/setup.bash 2>/dev/null || true
[ -f /opt/tros_jazzy/jazzy/local_setup.bash ] && source /opt/tros_jazzy/jazzy/local_setup.bash
cd /mnt/tros_jazzy/cc_ws/tros_ws/
[ -f install/setup.bash ] && source install/setup.bash
export ROS_DOMAIN_ID=77
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
RUN_ID=$(date +%Y%m%d_%H%M%S)
LOGDIR=/mnt/tros_jazzy/cc_ws/tros_ws/logs/rosmax_${RUN_ID}
mkdir -p $LOGDIR
timeout 10 ./install/lib/rosmax_nodes/test_subscriber --ros-args -p topic_name:=/rosmax/spike_0 > $LOGDIR/sub.log 2>&1 &
SUB=$!
sleep 2
timeout 8 ./install/lib/rosmax_nodes/test_publisher --ros-args -p topic_name:=/rosmax/spike_0 -p publish_rate_hz:=100.0 -p payload_size:=1024 > $LOGDIR/pub.log 2>&1 &
PUB=$!
wait $PUB 2>/dev/null
sleep 3
kill $SUB 2>/dev/null
echo "=== PUB (前3行) ==="; head -3 $LOGDIR/pub.log
echo "=== SUB STATS (末5行) ==="; grep STATS $LOGDIR/sub.log | tail -5
echo "LOGDIR=$LOGDIR"
EOF
