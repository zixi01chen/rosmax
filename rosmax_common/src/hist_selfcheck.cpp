// L-b 对拍自检（主机端运行，非板端）：确定性重尾合成分布 →
// 输出 1) 原始样本（供 Python hdrhistogram 重算）  2) 本实现百分位
// 用法: hist_selfcheck <n_samples> <raw_out_file>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cmath>

#include <rosmax_common/latency_histogram.hpp>

// xorshift64* — 确定性，跨平台一致
static uint64_t rng_state = 0x9E3779B97F4A7C15ull;
static uint64_t rng() {
  uint64_t x = rng_state;
  x ^= x >> 12; x ^= x << 25; x ^= x >> 27;
  rng_state = x;
  return x * 0x2545F4914F6CDD1Dull;
}
static double rng_unit() { return (rng() >> 11) * (1.0 / 9007199254740992.0); }

int main(int argc, char** argv) {
  const size_t n = argc > 1 ? strtoull(argv[1], nullptr, 10) : 200000;
  const char* out_path = argc > 2 ? argv[2] : "/tmp/hdr_raw.txt";

  // 重尾合成：95% 对数正态(μ=18.5≈180ms? no: 18.5 → exp(18.5)~1.1e8ns=108ms)
  // 设计成时延形态：体部 ~100μs，重尾拖到 ~50ms，1% 尾部稀疏采样到 ~200ms
  FILE* f = fopen(out_path, "w");
  if (!f) { perror("fopen"); return 1; }
  rosmax_common::LatencyHistogram hist(100, 1'000'000'000ull);  // 100ns ~ 1000s

  for (size_t i = 0; i < n; ++i) {
    const double u = rng_unit();
    double ns;
    if (u < 0.95) {
      // 对数正态体部：median 100μs
      const double z = (rng_unit() + rng_unit() + rng_unit() + rng_unit() + rng_unit() +
                        rng_unit() - 3.0) * 1.4142;  // 近似 N(0,1)
      ns = 100000.0 * exp(0.5 * z);
    } else {
      // Pareto 尾部 α=1.5，x_m = 300μs
      ns = 300000.0 / pow(rng_unit(), 1.0 / 1.5);
    }
    if (ns < 100) ns = 100;
    fprintf(f, "%llu\n", static_cast<unsigned long long>(ns));
    hist.record(static_cast<uint64_t>(ns));
  }
  fclose(f);

  printf("count=%llu\n", static_cast<unsigned long long>(hist.count()));
  printf("p50=%.0f\n", hist.percentile(50));
  printf("p95=%.0f\n", hist.percentile(95));
  printf("p99=%.0f\n", hist.percentile(99));
  printf("p999=%.3f\n", hist.percentile(99.9));
  printf("min=%llu\n", static_cast<unsigned long long>(hist.min()));
  printf("max=%llu\n", static_cast<unsigned long long>(hist.max()));
  return 0;
}
