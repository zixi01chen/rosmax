#!/usr/bin/env python3
"""板端状态采集器 v2：每秒汇总订阅节点 STATS + 全域话题发现 → status.json"""
import glob
import json
import os
import re
import subprocess
import time

OFFSETS = {}      # path -> 读取偏移
FILE_ROWS = {}    # path -> {from: (cum_recv, groups元组)}，跨秒保留最新值
LAST_NODES = {"rows": [], "t": 0.0, "data_ts": ""}  # 永久保留最后数据行（面板不清零，标注数据年龄）
TOPICS_TTL = 600.0  # 话题发现 10 分钟刷新一次（用户要求；topics.log 只读增量尾部）
TOPICS_STATE = {"t": 0.0, "offset": 0, "topics": []}

DIR = "/root/rosmax_live"
STATS_RE = re.compile(
    r"\[STATS\] to=(\d+) from=(\d+) recv=(\d+) rate=([\d.]+)Hz mean=([\d.]+) p50=([\d.]+) "
    r"p99=([\d.]+) p999=([\d.]+) min=(\d+) max=(\d+) loss=(\d+) disorder=(\d+) mismatch=(\d+)")
MESH_RE = re.compile(r"mesh_g(\d+)\.log")
ROS_NODE_RE = re.compile(r"\] \[([^\]]+)\]:")  # rclcpp 日志行 [时间] [节点名]:
TOPICS_RE = re.compile(r"\[TOPICS\] (.+)")

PROC_SNAP = {}  # pid -> {ticks, rss_kb, t}（上一轮进程采样，差分出 CPU%）
CLK = os.sysconf("SC_CLK_TCK")

DDR_STATE = {"t": 0.0}  # hrut_ddr 是 DDR 基准压测工具（主动生成内存流量）——低频采样，绝不每秒跑


def read_test_procs():
    """扫描 /proc：只统计本工具进程（overlay 路径的 mesh_group/topic_lister + 本脚本）。
    用户要求：CPU/内存只关注和测试相关的进程，板级负载由单独卡片呈现。"""
    me = os.getpid()
    out = {}
    for pd in glob.glob("/proc/[0-9]*"):
        pid = pd.split("/")[-1]
        if pid == str(me):
            continue
        try:
            with open(pd + "/cmdline", "rb") as f:
                cmd = f.read().decode(errors="ignore").replace("\0", " ")
        except OSError:
            continue
        if "/root/rosmax_overlay/" not in cmd:
            continue
        if "mesh_group" in cmd:
            name = "mesh_group"
        elif "topic_lister" in cmd:
            name = "topic_lister"
        else:
            continue
        try:
            with open(pd + "/stat") as f:
                parts = f.read().rsplit(")", 1)[1].split()
            ticks = int(parts[11]) + int(parts[12])  # utime + stime
            # /proc/pid/stat 去掉 "comm)" 后索引=字段号-3：rss 是字段 24 → parts[21]（页数）
            rss_kb = int(parts[21]) * (os.sysconf("SC_PAGE_SIZE") // 1024)
        except (OSError, ValueError, IndexError):
            continue
        out[pid] = {"name": name, "ticks": ticks, "rss_kb": rss_kb}
    return out


def read_ddr():
    """DDR 带宽（用户指定方法：hrut_ddr -t cpu -p 1000）。
    30s 采样一次：该工具持续打印 1ms 周期表格，且曾出现实例堆积加剧板负载
    （sshd 饿死事故），低频 + 短捕获 + 用超时后的部分输出，绝不长跑。"""
    now = time.time()
    if now - DDR_STATE["t"] < 30:
        return DDR_STATE.get("last")
    DDR_STATE["t"] = now
    try:
        r = subprocess.run(["hrut_ddr", "-t", "cpu", "-p", "1000"],
                           capture_output=True, text=True, timeout=2)
        out = r.stdout
    except subprocess.TimeoutExpired as e:  # 正常路径：采够 2s 就掐，用已产出的表格
        out = e.stdout or ""
        if isinstance(out, bytes):
            out = out.decode(errors="ignore")
    except Exception:
        DDR_STATE["last"] = None
        return None
    read_mbps = write_mbps = None
    for line in out.splitlines():  # 表行形如 " Read|  3211|  6254"，取 Bandwidth 列
        parts = [p.strip() for p in line.split("|")]
        if len(parts) == 3 and parts[0] == "Read":
            read_mbps = float(parts[2])
        elif len(parts) == 3 and parts[0] == "Write":
            write_mbps = float(parts[2])
    if read_mbps is None and write_mbps is None:
        DDR_STATE["last"] = None
        return None
    total = (read_mbps or 0) + (write_mbps or 0)
    DDR_STATE["last"] = {"read_mbps": read_mbps, "write_mbps": write_mbps,
                         "total_mbps": round(total, 1)}
    return DDR_STATE["last"]


def read_governor():
    try:
        with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor") as f:
            return f.read().strip()
    except OSError:
        return None


def read_temp():
    try:
        for z in sorted(glob.glob("/sys/class/thermal/thermal_zone*/temp")):
            with open(z) as f:
                return round(int(f.read().strip()) / 1000.0, 1)
    except OSError:
        pass
    return None


def ensure_topic_lister():
    """话题发现节点：不活着就拉起（输出到 topics.log）。
    环境必须自带（source ROS + DOMAIN_ID=77 + UDP profile）：writer 可能被
    任何方式启动（面板 ssh / 手动 setsid），继承环境不可靠——空 env 会让
    rclcpp 初始化失败即死，话题列表永远为空（2026-10-30 事故）。"""
    r = subprocess.run(["pgrep", "-f", "^/root/rosmax_overlay/lib/rosmax_nodes/topic_lister"],
                       capture_output=True)  # 锚定二进制：避免匹配到含关键字的 bash 残留
    if r.returncode != 0:
        cmd = ("source /opt/ros/jazzy/setup.bash && "
               "export AMENT_PREFIX_PATH=/root/rosmax_overlay:$AMENT_PREFIX_PATH "
               "LD_LIBRARY_PATH=/root/rosmax_overlay/lib:/opt/ros/jazzy/lib:$LD_LIBRARY_PATH "
               "ROS_DOMAIN_ID=77 "
               "FASTRTPS_DEFAULT_PROFILES_FILE=/root/rosmax_overlay/fastdds_udp_only.xml "
               "LC_ALL=C LANG=C && "
               "exec /root/rosmax_overlay/lib/rosmax_nodes/topic_lister")
        subprocess.Popen(["bash", "-c", cmd],
                         stdout=open(os.path.join(DIR, "topics.log"), "w"),
                         stderr=subprocess.STDOUT,
                         start_new_session=True)


def read_topics():
    # 10 分钟节流；但列表为空（如 topic_lister 刚拉起、发现收敛中）时 30s 重试，
    # 否则首次发现要干等一个完整 TTL
    now = time.time()
    ttl = TOPICS_TTL if TOPICS_STATE["topics"] else 30.0
    if now - TOPICS_STATE["t"] < ttl:
        return TOPICS_STATE["topics"]
    path = os.path.join(DIR, "topics.log")
    best = None
    try:
        size = os.path.getsize(path)
        with open(path, errors="ignore") as f:
            if size > 1_000_000:
                f.seek(size - 1_000_000)  # 只读尾部 1MB（覆盖最后几条 [TOPICS] 行）
            for line in f:
                m = TOPICS_RE.search(line)
                if m:
                    best = m
    except OSError:
        TOPICS_STATE["t"] = now
        return TOPICS_STATE["topics"]
    out = []
    if best:
        try:
            out = json.loads(best.group(1)).get("topics", [])
            for t in out:
                t["external"] = not t["name"].startswith("/rosmax/")
        except Exception:
            out = []
    TOPICS_STATE["t"] = now
    TOPICS_STATE["topics"] = out
    return out


def main():
    global PROC_SNAP  # main 内重新绑定快照；不声明会让前面的读取变成未绑定局部变量
    while True:
        try:
            with open(os.path.join(DIR, "config.json")) as f:
                cfg = json.load(f)
        except OSError:
            cfg = {"topics": 0, "rate_hz": 30, "size_bytes": 1024, "subs_per_topic": 1}

        ensure_topic_lister()

        # 话题发现强制刷新触发器：mesh 日志集合变化（拓扑变更）→ 本轮立即刷新一次，
        # 之后回到 10 分钟节流
        mesh_snapshot = tuple(sorted(glob.glob(os.path.join(DIR, "mesh_g*.log"))))
        if mesh_snapshot != TOPICS_STATE.get("mesh_snap"):
            TOPICS_STATE["mesh_snap"] = mesh_snapshot
            TOPICS_STATE["t"] = 0.0  # 强制下一轮 read_topics 立即刷新

        now_t = time.time()
        cur_procs = read_test_procs()
        proc_rows = []
        total_cpu = total_rss = 0.0
        for pid, c in cur_procs.items():
            prev = PROC_SNAP.get(pid)
            cpu_pct = None
            if prev and prev["ticks"] and now_t - prev["t"] > 0:
                cpu_pct = (c["ticks"] - prev["ticks"]) / CLK / (now_t - prev["t"]) * 100
            proc_rows.append({"name": c["name"], "pid": int(pid),
                              "cpu_pct": round(cpu_pct, 1) if cpu_pct is not None else None,
                              "rss_mb": round(c["rss_kb"] / 1024, 1)})
            if cpu_pct is not None:
                total_cpu += cpu_pct
            total_rss += c["rss_kb"] / 1024
        PROC_SNAP = {pid: {"ticks": c["ticks"], "rss_kb": c["rss_kb"], "t": now_t}
                     for pid, c in cur_procs.items()}
        ddr = read_ddr()

        nodes = []
        for log in sorted(glob.glob(os.path.join(DIR, "mesh_g*.log"))):
            m = MESH_RE.search(log)
            if not m:
                continue
            gi = m.group(1)
            key = log
            try:
                fsize = os.path.getsize(log)
            except OSError:
                FILE_ROWS.pop(key, None)
                OFFSETS.pop(key, None)
                continue
            off = OFFSETS.get(key, 0)
            if off > fsize:  # 日志被重建
                off = 0
                FILE_ROWS.pop(key, None)
            try:
                with open(log, errors="ignore") as f:
                    f.seek(off)
                    new = f.read()
                    OFFSETS[key] = f.tell()
            except OSError:
                continue
            rows = FILE_ROWS.setdefault(key, {})
            for line in new.splitlines():
                mm = STATS_RE.search(line)
                if mm:
                    key2 = (int(mm.group(1)), int(mm.group(2)))  # (to, from) 唯一键
                    rows[key2] = mm.groups()
            for key2 in sorted(rows):
                g = rows[key2]
                to, frm = key2
                nodes.append({"group": f"g{gi}", "topic": f"n{frm}_n{to}",
                              "src_node": f"rosmax_g{gi}_n{frm}",
                              "dst_node": f"rosmax_g{gi}_n{to}",
                              "recv": int(g[2]), "rate": float(g[3]),
                              "mean": float(g[4]), "p50": float(g[5]), "p99": float(g[6]),
                              "max": float(g[9]), "loss": int(g[10]), "disorder": int(g[11])})

        try:
            load = subprocess.check_output(["uptime"]).decode().split("load average:")[-1].strip()
        except Exception:
            load = ""

        # 保留最后一次数据行（永久）：拓扑切换/进程重启的窗口里面板不清零，
        # data_age_s 标注数据年龄，新数据到达后自动替换
        if nodes:
            LAST_NODES["rows"] = nodes
            LAST_NODES["t"] = now_t
            LAST_NODES["data_ts"] = time.strftime("%H:%M:%S")
        elif LAST_NODES["rows"]:
            nodes = LAST_NODES["rows"]
        data_age = int(now_t - LAST_NODES["t"]) if LAST_NODES["t"] else None
        status = {"ts": time.strftime("%H:%M:%S"), "config": cfg,
                  "nodes": nodes, "all_topics": read_topics(),
                  "data_ts": LAST_NODES["data_ts"], "data_age_s": data_age,
                  "cpu_percent": round(total_cpu, 1),  # 本工具全部进程合计（单核归一）
                  "mem": {"rss_mb": round(total_rss, 1)},
                  "test_procs": proc_rows, "ddr": ddr,
                  "load": load, "temp_c": read_temp(), "governor": read_governor()}
        tmp = os.path.join(DIR, "status.json.tmp")
        with open(tmp, "w") as f:
            json.dump(status, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(DIR, "status.json"))
        time.sleep(1)


if __name__ == "__main__":
    main()
