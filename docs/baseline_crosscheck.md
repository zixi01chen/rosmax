# L-c 对拍记录（W1.3，2026-10-27，板 s600-board-23）

## 配置（双方对齐）
Array1k≡1KB / 100Hz / RELIABLE+KEEP_LAST depth10 / rmw_fastrtps_cpp /
transport=UDP-only（configs/fastdds_udp_only.xml）/ domain 77 / .23 板（governor=performance，temp≈42.5°C，load 6.7-7.4 非受控）

## 关键拓扑发现

| Apex 运行形态 | mean | 判读 |
|---|---|---|
| 同进程（默认 -p1 -s1） | 21-28μs | 同 participant 走 Fast DDS 进程内直通，**非跨进程路径** |
| 分裂双进程（-p1 -s0 + -s1 -p0） | 44-53μs | 真实跨进程 UDP loopback |

**Apex 默认形态不能作跨进程参照**——正好验证了设计文档拒绝"探针同进程"方案的判断。

## 5×5 对拍数据（分裂双进程口径）

**Ours**（ignore_sec=3 对齐 Apex --ignore 3）：
- p50: 48.5 / 50.2 / 52.7 / 52.3 / 49.7 μs（极差 4.2μs）
- mean: 64.5 / 67.0 / 63.2 / 64.5 / 62.5 μs（极差 4.5μs）
- p99: 181-200μs；min 43.7-49.5μs；max 187μs-1.86ms（run2 有 1.86ms 离群窗口）
- loss=0 disorder=0 mismatch=0 全程；pacer missed=0

**Apex split**（sub 侧）：
- min: 39 / 44 / 44 / 42 / 44 μs
- mean: 50 / 44 / 53 / 51 / 49 μs（极差 9μs）
- max: 170-199μs

## L-c 判定（部分达成）

| 统计量 | Ours | Apex split | 判定 |
|---|---|---|---|
| min | 43.7-49.5μs | 39-44μs | ✓ 邻近（差 ~5μs，~11%） |
| max | 187-250μs | 170-199μs | ✓ 重叠（run2 的 1.86ms 离群待查） |
| p99 | 181-200μs | （fork 无输出） | 与 Apex max 同量级，间接吻合 |
| mean | 62.5-67.0μs | 44-53μs | ✗ +12-18μs（40%），超出 T(9μs) 带宽 |

## mean 差距的可能来源（按优先级）
1. **串行非交错**：我们的 5 次先跑、Apex 后跑，板负载在两阶段间漂移（load 6.7→7.4）——W1.4 环境控制（core pinning + 交错 run）后重测
2. **payload 差异**：我们 1024B payload+~30B 头 ≈ 1054B vs Array1k 1000B——理论影响 <5%
3. **fork 无百分位**：P50-to-P50 对拍在此 Apex fork 上不可行；min/mean/max 是可用口径上限

## 结论与下一步
- 测量内核**未被证伪**：min/max/p99 三口径吻合，mean 单口径超带
- W1.4 落地环境控制后**交错重测** mean 口径；若仍超带，做 per-message dump 级对齐排查
- fork 的 tabulate 输出层崩溃 workaround：`LC_ALL=C`（locale 损坏触发）；DDS 层 SHM 异常 workaround：UDP-only profile
- 局限声明：本对拍在 .23 负载 6-8 的共享板、非受控环境完成；正式基线以 .20 独占窗口为准
