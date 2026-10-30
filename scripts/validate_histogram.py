#!/usr/bin/env python3
"""L-b 对拍验证：自研对数桶(C++) vs Python hdrhistogram（纯 Python 参照实现）。

用法: validate_histogram.py <raw_file> <selfcheck_output_file>
判据: P999 相对误差 < 1%（另验 P50/P95/P99 作参考）
"""
import subprocess
import sys

REPO = "rosmax_common/src/hist_selfcheck.cpp"


def selfcheck(n, raw):
    subprocess.check_call(
        ["bash", "-c", f"g++ -O2 -I rosmax_common/include {REPO} -o /tmp/hist_selfcheck "
                       f"&& /tmp/hist_selfcheck {n} {raw} > /tmp/selfcheck_out.txt"])
    out = {}
    for line in open("/tmp/selfcheck_out.txt"):
        k, v = line.strip().split("=")
        out[k] = float(v)
    return out


def reference(raw):
    from hdrh.histogram import HdrHistogram
    h2 = HdrHistogram(100, 10**12, 2)
    with open(raw) as f:
        for line in f:
            h2.record_value(int(line.strip()))
    return {k: float(h2.get_value_at_percentile(p))
            for k, p in [("p50", 50.0), ("p95", 95.0), ("p99", 99.0), ("p999", 99.9)]}


def main():
    n = 200000
    raw = "/tmp/hdr_raw.txt"
    mine = selfcheck(n, raw)
    ref = reference(raw)
    print(f"{'指标':6} {'自研':>14} {'参照':>14} {'相对误差':>10}")
    failures = []
    for k in ("p50", "p95", "p99", "p999"):
        m, r = mine[k], ref[k]
        err = abs(m - r) / r if r else 0.0
        flag = ""
        if k == "p999" and err >= 0.01:
            failures.append(k)
            flag = "  ← FAIL"
        print(f"{k:6} {m:14.0f} {r:14.0f} {err*100:9.4f}%{flag}")
    if failures:
        print(f"\nL-b 验收: FAIL ({', '.join(failures)} 误差≥1%)")
        sys.exit(1)
    print(f"\nL-b 验收: PASS (P999 误差 < 1%, n={n})")


if __name__ == "__main__":
    main()
