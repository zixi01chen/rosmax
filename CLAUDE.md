# rosmax — ROS2 通信压力测试系统（Phase 1 / MVP）

对 RDK S600 开发板（aarch64, ROS2 Jazzy）做 Topic pub/sub 压测：时延百分位、吞吐、丢包。
设计文档（APPROVED）：`~/.gstack/projects/rosmax/zixi01.chen-unknown-design-20261027-132715.md`
CEO plan（MVP 范围）：`~/.gstack/projects/rosmax/ceo-plans/2026-10-27-rosmax-mvp.md`

## 环境事实（已核实/待核实，改代码前先看）

- 交叉编译容器是 **`czx_jazzy_container`**（ROS2 Jazzy）workspace s600 → `/mnt/tros/cc_ws_jazzy_s600/tros_ws`。
  build-tros-s600 skill 头部的 `czx_container` 是旧值，脚本 `CONTAINER` 变量需先修正。
- 板 `s600-board` = root@10.112.133.20；代码目录 NFS 双向挂载
  （host `/mnt/disk1/zixi01.chen/work/tros_jazzy/cc_ws/tros_ws` ↔ 板 `/mnt/zixi01.chen/tros_jazzy/cc_ws/tros_ws/`），
  结果目录板 `/mnt/zixi01.chen/data` ↔ host `/mnt/disk1/zixi01.chen/work/debugdata`。
- 板端路径另有 `/mnt/tros_jazzy/...` 写法、ROS_DOMAIN_ID 60/50 两处不一致 —— **W1.4 板上核实**。
- 时间戳：`CLOCK_MONOTONIC_RAW`，publish() 前写入 payload、callback 首行读取；不与 header.stamp 混用。
- transport 显式配置（udp/shm），每次 run 记录实际生效值；pub/sub 必须独立进程。

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- S600 交叉编译本仓库包 → invoke /build-tros-s600
- 板上运行/测试/收集日志 → invoke /run-tros-s600
- 远程机器执行（SSH/docker/板） → invoke /remote-exec
- Bugs/errors → invoke /investigate
- Save progress → invoke /context-save; Resume → invoke /context-restore
- Code review/diff check → invoke /review
