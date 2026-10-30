# rosmax_report

JSON → HTML 报告（延后 W3；MVP 仅产出 JSON）。

W3 交付内容（CEO plan 已接受项）：
- ReportGenerator：JSON → Plotly HTML（**Plotly inline 单文件自包含**，若 C6 重开则确认此项）
- **run-diff**：`rosmax-report diff run_A run_B` — 并排百分位/吞吐 + 环境快照对账（governor/transport/温度不一致直接判"不可比"）+ 阈值回归判定
- L-a 自洽校验：报告百分位 vs 原始时间戳重算，误差 <1%

JSON schema v1 从 MVP 第一版冻结，本包是 schema 的第一个消费者。
