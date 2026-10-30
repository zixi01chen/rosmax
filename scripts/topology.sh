#!/bin/bash
# 动态拓扑（组容器模型）：G 组 × 每组 N 节点（2~50），组内全互联
#   话题 /rosmax/g<g>/n<j>_n<k>；每组一个 mesh_group 进程（共享 participant）
# 用法: topology.sh ensure G N [RATE] [SIZE]
#   RATE/SIZE 与当前 config 不同 → 全量重生（发布节奏与消息体大小需重启生效）
[ "$1" = "ensure" ] && shift
G=${1:-1}
N=${2:-3}
DIR=/root/rosmax_live
NODES=$DIR/nodes
BIN=/root/rosmax_overlay/lib/rosmax_nodes

source /opt/ros/jazzy/setup.bash 2>/dev/null
ulimit -n 65535 2>/dev/null || true
export AMENT_PREFIX_PATH=/root/rosmax_overlay:$AMENT_PREFIX_PATH
export LD_LIBRARY_PATH=/root/rosmax_overlay/lib:/opt/ros/jazzy/lib:$LD_LIBRARY_PATH
export ROS_DOMAIN_ID=77
export FASTRTPS_DEFAULT_PROFILES_FILE=/root/rosmax_overlay/fastdds_udp_only.xml
export LC_ALL=C LANG=C

mkdir -p $NODES && cd $NODES
RATE=${3:-$(python3 -c "import json;print(json.load(open('$DIR/config.json'))['rate_hz'])" 2>/dev/null || echo 30)}
SIZE=${4:-$(python3 -c "import json;print(json.load(open('$DIR/config.json'))['size_bytes'])" 2>/dev/null || echo 1024)}

alive() { [ -f "$1" ] && kill -0 "$(cat "$1" 2>/dev/null)" 2>/dev/null; }

# 存活校验：死进程 pidfile 清理（自愈）+ 无 pidfile 孤儿按模式清杀
for f in g*.pid; do [ -e "$f" ] || continue; alive "$f" || rm -f "$f" "$DIR/mesh_${f%.pid}.log"; done
ORPHAN=$(pgrep -f "rosmax_overlay/lib/rosmax_nodes/mesh_group" 2>/dev/null | wc -l)
if [ "$ORPHAN" -gt "$(ls g*.pid 2>/dev/null | wc -l)" ]; then
  pkill -f "rosmax_overlay/lib/rosmax_nodes/mesh_group" 2>/dev/null
  rm -f g*.pid $DIR/mesh_g*.log
  ORPHAN=0
fi

curG=$(ls g*.pid 2>/dev/null | wc -l)
read -r curN curRATE curSIZE <<< "$(python3 -c "
import json
c=json.load(open('$DIR/config.json'))
print(c.get('nodes_per_group',0), c.get('rate_hz',0), c.get('size_bytes',0))" 2>/dev/null || echo "0 0 0")"

spawn_group() {  # $1=gid
  # 同 gid 幂等：已有存活实例则收养（并发 start 竞态防重）
  local EXIST=$(pgrep -f "mesh_group.*gid:=$1 " 2>/dev/null | head -1)
  if [ -n "$EXIST" ] && kill -0 "$EXIST" 2>/dev/null; then
    echo $EXIST > g$1.pid
    return
  fi
  # setsid + stdin 脱离：面板 POST 的 ssh 断开时会向进程组发 SIGHUP，
  # 不分离则 mesh_group 静默死亡（"设置节点数失败"的根因）
  setsid $BIN/mesh_group --ros-args \
      -p gid:=$1 -p n:=$N -p rate_hz:=${RATE}.0 -p payload_size:=$SIZE \
      -p qos_profile:=reliable_volatile_depth10 -p ignore_sec:=3.0 \
      > $DIR/mesh_g$1.log 2>&1 </dev/null &
  echo $! > g$1.pid
}
kill_group() {  # $1=gid
  [ -f g$1.pid ] && kill $(cat g$1.pid) 2>/dev/null
  rm -f g$1.pid $DIR/mesh_g$1.log
}

# N/RATE/SIZE 变化 → 全量重生（发布集合需重启）；G 变化 → 增量加减组（旧组不受影响）
FULL=0
[ "$curN" != "$N" ] && [ "$curN" != "0" ] && FULL=1
[ "$curRATE" != "$RATE" ] && [ "$curRATE" != "0" ] && FULL=1
[ "$curSIZE" != "$SIZE" ] && [ "$curSIZE" != "0" ] && FULL=1
if [ "$FULL" = "1" ]; then
  # 按命令行模式杀全部组进程——pidfile 可能已被 rm -rf 摧毁，按 pidfile 杀必漏
  pkill -f "rosmax_overlay/lib/rosmax_nodes/mesh_group" 2>/dev/null
  rm -f g*.pid $DIR/mesh_g*.log
  curG=0
fi

if [ "$G" -gt "$curG" ]; then
  for g in $(seq $curG $((G-1))); do spawn_group $g; done
elif [ "$G" -lt "$curG" ]; then
  for g in $(seq $G $((curG-1))); do kill_group $g; done
fi

python3 -c "
import json
c=json.load(open('$DIR/config.json'))
c['groups']=$G; c['nodes_per_group']=$N; c['rate_hz']=$RATE; c['size_bytes']=$SIZE
json.dump(c,open('$DIR/config.json','w'))"

# 状态采集器保活（幂等）：面板解耦后，ensure 是 writer 的唯一拉起点
if ! pgrep -f status_writer >/dev/null 2>&1; then
  setsid nohup python3 /root/rosmax_overlay/status_writer.py </dev/null >/dev/null 2>&1 &
fi
echo "topology: $G 组 × $N 节点/组（每组话题 $((N*(N-1)))，总话题 $(python3 -c "print($G*$N*($N-1))")）@ ${RATE}Hz ${SIZE}B"
