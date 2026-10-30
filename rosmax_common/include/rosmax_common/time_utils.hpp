#pragma once
// CLOCK_MONOTONIC_RAW：不受 NTP 步进影响，Phase 2+ 跨机可与 PTP 时基对齐。
// vDSO 读取，~20ns 级开销。不与 ROS header.stamp（system clock）混用。
#include <cstdint>
#include <ctime>

namespace rosmax_common {

/// 单调原始时钟，纳秒
inline uint64_t now_ns() {
  timespec ts;
  clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
  return static_cast<uint64_t>(ts.tv_sec) * 1'000'000'000ull +
         static_cast<uint64_t>(ts.tv_nsec);
}

}  // namespace rosmax_common
