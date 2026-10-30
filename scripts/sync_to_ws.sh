#!/bin/bash
# 把仓库包同步到交叉编译工作区（容器/板端命名空间下 symlink 不可解析，故用拷贝）。
# 用法: scripts/sync_to_ws.sh   （在仓库根执行；build 前必须先 sync）
set -e
REPO=$(cd "$(dirname "$0")/.." && pwd)
WS=/mnt/disk1/zixi01.chen/work/tros_jazzy/cc_ws/tros_ws/src/rosmax
mkdir -p "$WS"
for pkg in rosmax_common rosmax_nodes rosmax_metrics; do
  rsync -a --delete --exclude '.git' "$REPO/$pkg/" "$WS/$pkg/"
  echo "synced $pkg -> $WS/$pkg"
done
# 保险带：header-only 头直拷 install（colcon install 不刷新 root 属主的旧拷贝，
# 而 -isystem install/include/rosmax_common 优先命中 → 编译吃到旧头）。
# install 文件属 root（容器内构建产物），宿主机 cp 会被拒 → 必须经容器拷。
# 每次构建前强制刷新，杜绝"源已改、产物仍旧"的静默错位。
docker exec czx_jazzy_container bash -c "
  mkdir -p /mnt/tros_jazzy/cc_ws/tros_ws/install/include/rosmax_common && \
  cp -f /mnt/tros_jazzy/cc_ws/tros_ws/src/rosmax/rosmax_common/include/rosmax_common/*.hpp \
        /mnt/tros_jazzy/cc_ws/tros_ws/install/include/rosmax_common/
" && echo "headers forced into install/include/rosmax_common/"
