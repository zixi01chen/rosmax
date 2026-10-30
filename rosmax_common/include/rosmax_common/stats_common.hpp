#pragma once
// 订阅端统计结构（MVP：JSON 由驱动脚本从心跳/汇总行组装；W3 由 orchestrator 聚合）
#include <cstdint>

namespace rosmax_common {

struct SubscriberStats {
  // 计数
  uint64_t total_received{0};
  uint64_t total_expected{0};   // 由 seq 最大值+1 推断（W2 由驱动端发布数对账）
  uint64_t total_lost{0};
  uint64_t total_disorder{0};   // 乱序计数
  uint64_t payload_len_mismatch{0};

  // 时延（纳秒）—— MVP spike 由 LatencyHistogram 导出
  double p50_ns{0};
  double p95_ns{0};
  double p99_ns{0};
  double p999_ns{0};
  uint64_t min_ns{0};
  uint64_t max_ns{0};

  // 时间窗口
  uint64_t window_start_ns{0};
  uint64_t window_end_ns{0};
  double actual_rate_hz{0};     // 实测接收速率
};

}  // namespace rosmax_common
