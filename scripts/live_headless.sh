#!/bin/bash
# 板端压测 runner（无界面，网格模型）：G 组 × 每组 N 节点组内全互联
# 用法: live_headless.sh start [组数G] [组内节点数N(2~10)] [频率Hz] [payload字节]
#       live_headless.sh stop
ACTION=$1
G=${2:-1}
N=${3:-3}
RATE=${4:-200}
SIZE=${5:-1024}
DIR=/root/rosmax_live

source /opt/ros/jazzy/setup.bash 2>/dev/null
# 大拓扑 fd 上限：50 节点组 ≈ 4900 socket，默认 1024 必炸
ulimit -n 65535 2>/dev/null || true
export AMENT_PREFIX_PATH=/root/rosmax_overlay:$AMENT_PREFIX_PATH
export LD_LIBRARY_PATH=/root/rosmax_overlay/lib:/opt/ros/jazzy/lib:$LD_LIBRARY_PATH
export ROS_DOMAIN_ID=77
export FASTRTPS_DEFAULT_PROFILES_FILE=/root/rosmax_overlay/fastdds_udp_only.xml
export LC_ALL=C LANG=C

if [ "$ACTION" = "stop" ]; then
  pkill -f mesh_group 2>/dev/null
  pkill -f status_writer 2>/dev/null
  pkill -f "rosmax_overlay/lib/rosmax_nodes/topic_lister" 2>/dev/null
  rm -rf $DIR/nodes $DIR/mesh_*.log $DIR/status.json $DIR/topics.log
  rm -f /dev/shm/fastrtps_* /dev/shm/fast_datasharing* 2>/dev/null
  echo "stopped"
  exit 0
fi

mkdir -p $DIR && cd $DIR
# 并发 start 防重：文件锁串行化（面板自动 start 与手动 start 竞态曾产生双发布者）
# 并发 start 防重：flock 串行化（板上 /usr/bin/flock 已确认存在）
# 防重复启动 + 深度清场（防双发布污染统计 + 清 /dev/shm fastrtps 残段）
pkill -f "rosmax_overlay/lib/rosmax_nodes/mesh_group" 2>/dev/null
pkill -f status_writer 2>/dev/null
pkill -f "rosmax_overlay/lib/rosmax_nodes/topic_lister" 2>/dev/null
rm -f /dev/shm/fastrtps_* /dev/shm/fast_datasharing* 2>/dev/null
rm -rf $DIR/nodes
sleep 1
rm -f mesh_*.log status.json topics.log
echo "{\"groups\": $G, \"nodes_per_group\": $N, \"rate_hz\": $RATE, \"size_bytes\": $SIZE}" > config.json

# 拓扑由 topology.sh 统一管理
bash /root/rosmax_overlay/topology.sh ensure $G $N

python3 /root/rosmax_overlay/status_writer.py
rmdir $DIR/.start.lock 2>/dev/null
