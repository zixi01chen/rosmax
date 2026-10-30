#pragma once
// 时延直方图 — 自研对数桶（HDR 语义），2026-10-27 W1.1b 决策：
//   HdrHistogram_c 因网络封锁无法获取（github clone/curl/ghproxy/gitee 四路全断，0.5 天预算内），
//   按设计文档降级路径走自研对数桶，L-b 验收 = 与 Python hdrhistogram 对拍 P999 < 1%。
//   若日后能手工 vendor HdrHistogram_c，本类接口（record/percentile/count/min/max/reset）不变可直换。
//
// 结构：HDR 式两级桶 —— log2 对数桶 × 每桶 sub_count 个线性槽。
//   桶 b 覆盖 [lowest << b, lowest << (b+1))，桶宽 w = lowest << b，
//   槽 j 覆盖 [lo + j*w/sub_count, lo + (j+1)*w/sub_count)。
// 内存：默认参数下 ~66 桶 × 2048 槽 × 4B ≈ 540KB，覆盖 100ns ~ 1000s。
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>

namespace rosmax_common {

class LatencyHistogram {
 public:
  explicit LatencyHistogram(uint64_t lowest_discernible_ns = 100,
                            uint64_t max_trackable_ns = 1'000'000'000'000ull)
      : lowest_(lowest_discernible_ns), max_(max_trackable_ns) {
    const int b = (lowest_discernible_ns >= max_trackable_ns)
                      ? 1
                      : static_cast<int>(63 - __builtin_clzll(max_trackable_ns /
                                                              lowest_discernible_ns)) + 2;
    bucket_count_ = static_cast<size_t>(b);
    counts_.assign(bucket_count_ * kSubCount, 0);
  }

  void record(uint64_t v) {
    ++total_;
    if (total_ == 1 || v < min_) min_ = v;
    if (total_ == 1 || v > observed_max_) observed_max_ = v;
    if (v < lowest_) v = lowest_;
    size_t b;
    if (v < lowest_ * 2) {
      b = 0;
    } else {
      // 桶索引 = floor(log2(v / lowest))
      b = static_cast<size_t>(63 - __builtin_clzll(v / lowest_));
    }
    const uint64_t lo = lowest_ << b;
    const uint64_t w = lo;  // 桶宽 = 桶下界（log2 结构）
    uint64_t off = v - lo;
    if (v >= max_) {  // 超上限：归入最后一桶最后一槽（overflow 语义）
      b = bucket_count_ - 1;
      off = w - 1;
    }
    size_t slot = b * kSubCount + static_cast<size_t>((off * kSubCount) / w);
    if (slot >= counts_.size()) slot = counts_.size() - 1;
    ++counts_[slot];
  }

  uint64_t count() const { return total_; }
  uint64_t min() const { return total_ ? min_ : 0; }
  uint64_t max() const { return total_ ? observed_max_ : 0; }
  void reset() {
    std::fill(counts_.begin(), counts_.end(), 0);
    total_ = 0;
    min_ = 0;
    observed_max_ = 0;
  }

  /// 线性插值百分位，p ∈ [0,100]。
  /// 精度：槽相对宽度 ≤ 1/sub_count（~0.05%），远优于 L-a/L-b 的 1% 门槛。
  double percentile(double p) const {
    if (total_ == 0) return 0.0;
    const double target = (p / 100.0) * static_cast<double>(total_);
    uint64_t cum = 0;
    for (size_t b = 0; b < bucket_count_; ++b) {
      const uint64_t lo = lowest_ << b;
      const uint64_t w = lo;
      for (size_t j = 0; j < kSubCount; ++j) {
        const uint64_t c = counts_[b * kSubCount + j];
        if (c == 0) continue;
        const double cum_after = static_cast<double>(cum + c);
        if (cum_after >= target) {
          // 槽内线性插值：槽 j 值域 [lo + j*w/S, lo + (j+1)*w/S)
          const double lo_v = static_cast<double>(lo) +
                              static_cast<double>(j) * static_cast<double>(w) / kSubCount;
          const double slot_w = static_cast<double>(w) / kSubCount;
          const double frac =
              c ? (target - static_cast<double>(cum)) / static_cast<double>(c) : 0.0;
          return lo_v + frac * slot_w;
        }
        cum += c;
      }
    }
    return static_cast<double>(observed_max_);
  }

  static constexpr size_t kSubCount = 2048;

 private:
  uint64_t lowest_;
  uint64_t max_;            // 可跟踪上限（配置）
  size_t bucket_count_;
  uint64_t total_{0};
  uint64_t min_{0};
  uint64_t observed_max_{0};  // 实际观测最大值
  std::vector<uint32_t> counts_;
};

}  // namespace rosmax_common
