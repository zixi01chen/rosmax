# rosmax — ROS2 Communication Stress-Test & Live Monitoring Platform

English | [中文](README_cn.md)


**Runs on your x86 machine, connects to an aarch64 board (e.g. RDK S600) over SSH, spins up a full-mesh stress topology with one command, and gives you a browser dashboard showing per-link latency, frequency, and packet loss in real time.**

中文说明：[README_cn.md](README_cn.md)

## What Problem It Solves

"Can the inter-node communication hold up?" shouldn't be a guess. rosmax generates a **G groups × N nodes per group** full-mesh stress topology on the target board (every pair of nodes in a group gets a dedicated directional topic, direction encoded in the name like `n0_n1`), then gives you a browser dashboard that answers in real time:

- Latency distribution per link (mean / p50 / p99 / max)
- Actual per-topic receive rate vs configured rate
- Packet loss count and loss rate (honest degradation under overload)
- Total network throughput, board temperature, board load

## Architecture

```
┌────────── x86 machine ──────────┐        ┌──── aarch64 board ────┐
│  panel.py  (:8090 web panel)    │  SSH   │  mesh_group × G groups │
│   ├─ polls status.json @1Hz     │ ◀───── │   ├─ full-mesh pub/sub │
│   ├─ Top10 latency + trend      │        │   └─ status_writer.py  │
│   └─ topology control (apply)   │ ─────▶ │      └─ 1s status snap │
│  browser ← http://localhost:8090│  POST  │  (rate/size/groups     │
└──────────────────────────────────┘        │   all adjustable)      │
                                            └────────────────────────┘
```

- **Board side**: one `mesh_group` process hosts a group of logical nodes (unique ROS node names, shared DDS participant); messages use standard `std_msgs/msg/String` with a 42-byte fixed text header carrying seq + timestamp — any ROS2 tool can echo the topics directly
- **Host side**: the panel polls a status snapshot over SSH every second; topology changes (groups / nodes / rate / size) go through the "Apply" button and take effect in seconds
- **Decoupled by design**: restarting the panel never touches board-side stress; board-side stress is controlled from the web UI

## Quick Start

Prerequisites: x86 machine can SSH to the board as root; `/root/rosmax_overlay` deployed on the board (see docs/).

```bash
# 1. Launch the panel (spawns 1 group × 3 nodes @30Hz on the board by default)
python3 scripts/panel.py

# 2. Open in browser
#    http://localhost:8090

# 3. Adjust topology on the page: groups G / nodes per group N / rate / size → Apply
#    Example: G=2, N=10, 100Hz — 2 groups × 10 nodes, 90 topics per group, 180 total
```

## Dashboard Reference

| Area | Content |
|---|---|
| Top cards | total nodes / total topics / per-topic rate / total throughput / cumulative loss / **loss rate** / board temp / board load |
| Stress performance | one row per topic (source→dest), sorted by latency desc, **paginated to view all**; includes loss-rate column |
| Trend chart | p50 trend of the top-10 highest-latency topics (60s window) |
| All topics on device | every topic in the same domain (incl. external systems), **10 per page + pagination**, refreshed every 10 minutes |
| Topology control | groups / nodes / rate / size inputs + apply and quick ±1 buttons |

Data freshness badge: Live (green) / Historical data · Xs ago (amber — normal during topology build-out).

## Metrics Explained

- **Latency** = receive-callback-entry time − encode-before-publish time (same-board CLOCK_MONOTONIC_RAW, no cross-machine sync issues)
- **p50 / p99 / max** = percentiles of all messages in the window — p50 for the typical case, p99 for the worst 1%
- **Loss rate** = loss / (recv + loss), detected by sequence-number continuity
- **rate** = actual per-topic receive rate; under overload the pacer automatically degrades (overload protection, honest numbers)

## Capacity Reference (RDK S600 measured, 1KB payload)

| Topology | Topics | Rate | Result |
|---|---|---|---|
| 1×3 | 6 | 200Hz | p50 49-91μs, zero loss ✓ |
| 10×10 (100 nodes) | 900 | 100Hz | rate 97Hz, zero loss ✓ |
| 1×50 | 2450 | 10Hz | rate 8Hz, p50 ~88ms ⚠ |
| 100×10 (1000 nodes) | 9000 | 10Hz | full match, at resource edge ⚠ |

Rule of thumb: for μs-level latency, keep each group at `N×(N-1)×rate ≤ 20,000 msg/s`; board-wide ≤ 90,000 msg/s. See [docs/w2_acceptance.md](docs/w2_acceptance.md).

## Repository Layout

```
scripts/panel.py            host-side web panel (:8090)
scripts/live_headless.sh    board-side stress lifecycle (start/stop, deep clean built in)
scripts/topology.sh         board-side topology manager (ensure G N [RATE] [SIZE], idempotent)
scripts/status_writer.py    board-side status collector (writes status.json @1s)
rosmax_nodes/src/mesh_group.cpp   board-side group-container stress process
docs/                       design docs and test records
```

## Known Limitations

- Single group process handles ~2500 msg/s under board-shared load; scale with multiple groups
- Board reboots do not auto-restore running stress — one click on Apply restores the configured topology
- FastDDS SHM transport has known issues on some boards — the system forces UDP single transport

## Docs

- [Design & acceptance records](docs/w2_acceptance.md) — test matrix, capacity model, post-mortems
- [Board profile](docs/board_profile.json) — board environment facts
