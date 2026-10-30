# 工具链侦察记录（W1.1a，2026-10-27）

## 路径关系（已核实）

```
HOST /mnt/disk1/zixi01.chen/work  ──docker mount──▶  CONTAINER /mnt
HOST /mnt/disk1/zixi01.chen/work/tros_jazzy/cc_ws/tros_ws
      ▲                                    │
      │ NFS（host↔board 双向）              │ 容器内路径
      ▼                                    ▼
BOARD /mnt/zixi01.chen/tros_jazzy/cc_ws/tros_ws/   CONTAINER /mnt/tros_jazzy/cc_ws/tros_ws
                                                    （build-tros-s600.sh 的工作区 ✓）
```

- **build 工作区 = NFS 目录本身**：容器内构建 → install 产物直接出现在板端，无 rsync 环节（设计文档预判正确）。
- 容器内另有 `/mnt/tros/cc_ws_jazzy_s600/tros_ws`（machines.yaml 记录的路径，本机不经过 NFS）——本 MVP **不使用**，machines.yaml 该条目视为过期信息，W1.4 一并记录。
- `build-tros-s600.sh` 已是修正版（CONTAINER=czx_jazzy_container，cd /mnt/tros_jazzy/cc_ws/tros_ws），无需再改。

## 容器内容（czx_jazzy_container，Up）

- `/opt/ros/jazzy` ✓
- `/usr/bin/aarch64-linux-gnu-gcc` ✓（交叉编译器）
- 工作区内 `tros_arm_build/`（S600 构建支撑）✓
- `robot_dev_config/build.sh` ✓，构建入口：`build.sh -p S600 -r '--parallel-workers 1 --packages-select <PKG>'`

## src 布局惯例

`src/ros/`= 基础设施（trosdep/cross_compile/rosdistro）；产品包在 `src/tros/`。
rosmax 包放置：**`src/rosmax/`**（新类别，不污染现有两类）。

## 板（10.112.133.20）

- SSH 22 端口可达（root/root，见 machines.yaml `s600-board`）
- 详细画像（核数/内存/governor/rclpy/RMW/domain_id）→ W1.4

## 待办迁移

- HDR_c 源码获取：GitHub 直连在此网络不稳定（SSL EOF），尝试镜像；0.5 天上限，失败即走自研对数桶路线（设计文档内建降级路径）
