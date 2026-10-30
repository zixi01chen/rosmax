"""MVP sweep 驱动：在板端启动 pub/sub 子进程，收集 STATS 输出 + /proc 指标，落盘 JSON 结果。

JSON schema v1（冻结——CI 回归 diff 与 W3 run-diff 都消费此 schema，改字段先升版本号）。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time

NODES_BIN = os.environ.get("NODES_BIN", "install/lib/rosmax_nodes")
STATS_RE = re.compile(
    r"\[STATS\] recv=(\d+) rate=([\d.]+)Hz mean=([\d.]+) p50=([\d.]+) p99=([\d.]+) p999=([\d.]+) "
    r"min=(\d+) max=(\d+) loss=(\d+) disorder=(\d+) mismatch=(\d+)")
PACER_RE = re.compile(r"\[PACER\] missed=(\d+) of (\d+)")
RTT_RE = re.compile(r"\[RTT\] n=(\d+) mean=([\d.]+) min=(\d+) max=(\d+)")


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def env_snapshot(rmw: str) -> dict:
    governor = _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    temp = None
    for zone in sorted(os.listdir("/sys/class/thermal")):
        try:
            with open(f"/sys/class/thermal/{zone}/temp") as f:
                temp = int(f.read().strip()) / 1000.0
            break
        except (OSError, ValueError):
            continue
    loadavg = _read("/proc/loadavg")
    return {
        "hostname": os.uname().nodename,
        "uname": " ".join(os.uname()),
        "nproc": os.cpu_count(),
        "mem_mb": _mem_mb(),
        "governor": governor,
        "cpu_temp_c": temp,
        "load1": loadavg.split()[0] if loadavg else None,
        "ROS_DOMAIN_ID": os.environ.get("ROS_DOMAIN_ID"),
        "RMW_IMPLEMENTATION": os.environ.get("RMW_IMPLEMENTATION"),
        "dds_xml_digest": _digest(rmw),
    }


def _mem_mb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def _digest(rmw: str):
    env_key = {"cyclonedds": "CYCLONEDDS_URI", "fastdds": "FASTRTPS_DEFAULT_PROFILES_FILE",
               "fastrtps": "FASTRTPS_DEFAULT_PROFILES_FILE"}
    path = os.environ.get(env_key.get(rmw.replace("rmw_", "").replace("_cpp", ""), ""), "")
    if path and os.path.isfile(path):
        with open(path, "rb") as f:
            return f"{path}:{hashlib.sha256(f.read()).hexdigest()[:16]}"
    return "default"


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"


def parse_stats(text: str) -> dict:
    """取 recv 最大的 STATS 行（pub 停止后的空窗口重置行会排空直方图，须跳过）"""
    best, best_recv = None, -1
    for m in STATS_RE.finditer(text):
        recv = int(m.group(1))
        if recv > best_recv:
            best, best_recv = m, recv
    if best is None:
        return {}
    (recv, rate, mean, p50, p99, p999,
     vmin, vmax, loss, disorder, mismatch) = best.groups()
    return {"n_received": int(recv), "rate_actual_hz": float(rate),
            "mean_ns": float(mean), "p50_ns": float(p50), "p99_ns": float(p99),
            "p999_ns": float(p999), "min_ns": float(vmin), "max_ns": float(vmax),
            "loss": int(loss), "disorder": int(disorder), "mismatch": int(mismatch)}


def parse_pub(text: str) -> dict:
    out = {}
    m = PACER_RE.search(text)
    if m:
        missed, total = int(m.group(1)), int(m.group(2))
        out["missed_publish_pct"] = round(100.0 * missed / total, 4) if total else 0.0
    r = RTT_RE.search(text)
    if r:
        out["rtt"] = {"n": int(r.group(1)), "mean_ns": float(r.group(2)),
                      "min_ns": float(r.group(3)), "max_ns": float(r.group(4))}
    return out


def _proc_stat(pid: int):
    """(cpu_ticks, rss_kb)；stat 的 comm 字段含括号需从右切"""
    try:
        with open(f"/proc/{pid}/stat") as f:
            parts = f.read().rsplit(")", 1)[1].split()
        ticks = int(parts[11]) + int(parts[12])   # utime+stime
        rss_kb = int(parts[23]) * (os.sysconf("SC_PAGE_SIZE") // 1024)
        return ticks, rss_kb
    except (OSError, IndexError, ValueError):
        return None


def sample_metrics(pids) -> dict:
    clk = os.sysconf("SC_CLK_TCK")
    out = {}
    for pid in pids:
        s = _proc_stat(pid)
        if s:
            out[pid] = {"cpu_ticks": s[0], "rss_kb": s[1], "t": time.monotonic()}
    return out if out else {}


def finish_metrics(first: dict) -> dict:
    clk = os.sysconf("SC_CLK_TCK")
    out = {}
    for pid, v in first.items():
        s = _proc_stat(pid)
        if not s:
            continue
        dt = time.monotonic() - v["t"]
        cpu_pct = (s[0] - v["cpu_ticks"]) / clk / dt * 100.0 if dt > 0 else None
        out[str(pid)] = {"cpu_pct_onecore": round(cpu_pct, 2) if cpu_pct is not None else None,
                         "max_rss_kb": max(v["rss_kb"], s[1])}
    return out


def run_once(args, size: int, rate: float, qos: str, transport: str,
             topic: str, result_dir: str, base_env: dict) -> dict:
    """单次 run：起 sub(+echo)/pub(+rtt)，采样，解析，落 JSON"""
    started = utcnow()
    env = dict(base_env)
    # pin 语义："2,3" = sub 钉核 2、pub 钉核 3（分离独占，避免双进程共享双核互踩）
    pin_sub = ["taskset", "-c", args.pin_cpus.split(",")[0]] if args.pin_cpus else []
    pin_pub = ["taskset", "-c", args.pin_cpus.split(",")[1]] if (args.pin_cpus and "," in args.pin_cpus) else pin_sub
    tag = f"{size}B_{int(rate)}Hz"
    env["FASTRTPS_DEFAULT_PROFILES_FILE"] = _profile_for(transport, result_dir)
    env["CYCLONEDDS_URI"] = _cyclone_profile_for(transport, result_dir)

    sub_cmd = pin_sub + [f"{args.nodes_bin}/test_subscriber", "--ros-args",
                         "-p", f"topic_name:={topic}",
                         "-p", f"qos_profile:={qos}",
                         "-p", f"ignore_sec:={args.ignore}",
                         "-p", "window_size_sec:=1.0"]
    pub_cmd = pin_pub + [f"{args.nodes_bin}/test_publisher", "--ros-args",
                     "-p", f"topic_name:={topic}",
                     "-p", f"qos_profile:={qos}",
                     "-p", f"payload_size:={size}",
                     "-p", f"publish_rate_hz:={rate}"]
    if args.rtt:
        pub_cmd += ["-p", "enable_rtt:=true"]
        sub_cmd += ["-p", "enable_echo:=true"]

    procs, logs = [], {}
    metrics_first = {}
    try:
        logs["sub"] = open(os.path.join(result_dir, f"sub_{tag}.log"), "w")
        logs["pub"] = open(os.path.join(result_dir, f"pub_{tag}.log"), "w")
        procs.append(subprocess.Popen(sub_cmd, stdout=logs["sub"], stderr=logs["sub"], env=env))
        time.sleep(args.discovery)
        procs.append(subprocess.Popen(pub_cmd, stdout=logs["pub"], stderr=logs["pub"], env=env))
        time.sleep(args.discovery)
        metrics_first = sample_metrics([p.pid for p in procs])
        time.sleep(args.warmup + args.duration)
        # SIGINT 前采样 CPU/RSS（进程退出后 /proc/<pid> 消失）
        metrics_final = finish_metrics(metrics_first)
    finally:
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGINT)
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        for f in logs.values():
            f.close()

    sub_text = open(os.path.join(result_dir, f"sub_{tag}.log")).read()
    pub_text = open(os.path.join(result_dir, f"pub_{tag}.log")).read()
    results = parse_stats(sub_text)
    results.update(parse_pub(pub_text))
    results["metrics"] = metrics_final
    if results:
        results["n_expected"] = results.get("n_received", 0) + results.get("loss", 0)

    report = {
        "schema_version": 1,
        "test_id": args.test_id,
        "git_commit": git_commit(),
        "config": {"size_bytes": size, "rate_hz": rate, "qos": qos,
                   "transport_requested": transport, "rmw": args.rmw,
                   "domain_id": args.domain, "discovery_sec": args.discovery,
                   "warmup_sec": args.warmup, "duration_sec": args.duration,
                   "pin_cpus": args.pin_cpus, "rtt": args.rtt,
                   "nodes_bin": args.nodes_bin},
        "env": env_snapshot(args.rmw),
        "transport_effective": transport if transport != "default" else "unknown",
        "results": results,
        "run": {"started_utc": started, "ended_utc": utcnow(), "result_dir": result_dir},
    }
    out_path = os.path.join(result_dir, f"result_{tag}.json")
    with open(out_path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[DRIVER] {tag} -> {out_path}", flush=True)
    print("  " + json.dumps(results.get("metrics", {})), flush=True)
    return report


def _profile_for(transport: str, result_dir: str) -> str:
    """transport 强制：udp/shm 对应的 FastDDS XML；写入 result_dir 供回读摘要"""
    if transport not in ("udp", "shm"):
        return os.environ.get("FASTRTPS_DEFAULT_PROFILES_FILE", "")
    path = os.path.join(result_dir, f"fastdds_{transport}.xml")
    if not os.path.exists(path):
        if transport == "udp":
            body = UDP_PROFILE
        else:
            body = SHM_PROFILE
        with open(path, "w") as f:
            f.write(body)
    return path


def _cyclone_profile_for(transport: str, result_dir: str) -> str:
    if transport not in ("udp", "shm"):
        return os.environ.get("CYCLONEDDS_URI", "")
    path = os.path.join(result_dir, f"cyclonedds_{transport}.xml")
    if not os.path.exists(path):
        net = '<NetworkInterfaceAddress>auto</NetworkInterfaceAddress>' if transport == "udp" \
            else '<NetworkInterfaceAddress>lo</NetworkInterfaceAddress><AllowMulticast>spdp</AllowMulticast>'
        with open(path, "w") as f:
            f.write(f'<CycloneDDS xmlns="urn:CycloneDDS:Config"><Domain>'
                    f'<General><Interfaces><Public>{net}</Public></Interfaces>'
                    f'<AllowMulticast>spdp</AllowMulticast></General></Domain></CycloneDDS>')
    return path


UDP_PROFILE = """<?xml version="1.0" encoding="UTF-8" ?>
<dds xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">
  <profiles>
    <transport_descriptors>
      <transport_descriptor>
        <transport_id>udp_v4_only</transport_id>
        <type>UDPv4</type>
      </transport_descriptor>
    </transport_descriptors>
    <participant profile_name="udp_only_participant" is_default_profile="true">
      <rtps>
        <userTransports><transport_id>udp_v4_only</transport_id></userTransports>
        <useBuiltinTransports>false</useBuiltinTransports>
      </rtps>
    </participant>
  </profiles>
</dds>
"""

SHM_PROFILE = """<?xml version="1.0" encoding="UTF-8" ?>
<!-- SHM-only：Builtin 下保留 SHM、去 UDP（同机 SHM 路径口径） -->
<dds xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">
  <profiles>
    <transport_descriptors>
      <transport_descriptor>
        <transport_id>shm_only</transport_id>
        <type>SHM</type>
      </transport_descriptor>
    </transport_descriptors>
    <participant profile_name="shm_only_participant" is_default_profile="true">
      <rtps>
        <userTransports><transport_id>shm_only</transport_id></userTransports>
        <useBuiltinTransports>false</useBuiltinTransports>
      </rtps>
    </participant>
  </profiles>
</dds>
"""


def main():
    ap = argparse.ArgumentParser(description="rosmax MVP sweep driver")
    ap.add_argument("--test-id", default="adhoc")
    ap.add_argument("--nodes-bin", default=NODES_BIN)
    ap.add_argument("--sizes", default="1024", help="逗号分隔，字节")
    ap.add_argument("--rates", default="100", help="逗号分隔，Hz")
    ap.add_argument("--qos", default="reliable_volatile_depth10")
    ap.add_argument("--transports", default="default", help="逗号分隔: udp,shm,default")
    ap.add_argument("--rmw", default=os.environ.get("RMW_IMPLEMENTATION", "rmw_fastrtps_cpp"))
    ap.add_argument("--domain", type=int, default=77)
    ap.add_argument("--discovery", type=float, default=3.0)
    ap.add_argument("--warmup", type=float, default=3.0)
    ap.add_argument("--ignore", type=float, default=3.0)
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--result-dir", default="/root/rosmax_results")
    ap.add_argument("--pin-cpus", default="", help="如 '2,3'：sub 钉第一个核、pub 钉第二个核")
    ap.add_argument("--rtt", action="store_true", help="启用 RTT/2 校准（echo 回环）")
    args = ap.parse_args()

    sizes = [int(x) for x in args.sizes.split(",")]
    rates = [float(x) for x in args.rates.split(",")]
    transports = [t.strip() for t in args.transports.split(",")]

    os.makedirs(args.result_dir, exist_ok=True)
    base_env = dict(os.environ)
    base_env["ROS_DOMAIN_ID"] = str(args.domain)
    base_env["RMW_IMPLEMENTATION"] = args.rmw

    n = 0
    failed = 0
    for transport in transports:
        for size in sizes:
            for rate in rates:
                n += 1
                topic = f"/rosmax/t_{n}"
                r = run_once(args, size, rate, args.qos, transport,
                             topic, args.result_dir, base_env)
                if not r["results"] or r["results"].get("n_received", 0) == 0:
                    failed += 1
    print(f"[DRIVER] 完成 {n} 组配置，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
