// mesh_group — 组容器进程（1000 节点目标架构，2026-10-27）。
//
//   一个进程承载一个组：组内 N 个逻辑节点（各自独立 ROS 节点名），共享一个
//   DDS participant → 1000 节点 = 100 进程/100 participant。进程模型下 1000 个
//   participant 的内存（20-40MB/个）与发现风暴都不可行。
//
//   组内全互联：节点 j 发布 N-1 个话题 /rosmax/g<g>/n<j>_n<k>，订阅 N-1 个
//   /rosmax/g<g>/n<k>_n<j>。每组话题数 = N×(N-1) > 节点数 N，方向含于话题名。
//
// 测量口径：单来源——每条话题独立直方图/seq/丢包，每秒各打一行
// [STATS] to=<dst> from=<src> ...，行率 = 该话题真实频率（=配置频率）。
//
// stats_ 下标公式：按 (j,k) j=0..N-1, k=0..N-1, k≠j 生成序，即
//   idx = j*(N-1) + (k > j ? k-1 : k)
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include <rclcpp/rclcpp.hpp>
#include <rosmax_common/latency_histogram.hpp>
#include <rosmax_common/qos_profiles.hpp>
#include <rosmax_common/string_protocol.hpp>
#include <rosmax_common/time_utils.hpp>
#include <std_msgs/msg/string.hpp>

using std_msgs::msg::String;

namespace {

constexpr size_t HDR = rosmax_common::STRING_HDR_LEN;

// --ros-args -p key:=value 的极简解析（本进程无默认节点，直接读 argv）
std::string arg_of(int argc, char** argv, const std::string& key,
                   const std::string& fallback) {
  const std::string prefix = key + ":=";
  for (int i = 1; i + 1 < argc; ++i) {
    const std::string a = argv[i];
    const std::string v = argv[i + 1];
    if (a == "-p" && v.rfind(prefix, 0) == 0) {
      return v.substr(prefix.size());
    }
  }
  return fallback;
}

struct TopicStat {  // 一条有向话题（from→to）的独立统计
  int from{0}, to{0};
  uint64_t expected_seq{0}, received{0}, received_win{0}, lost{0}, disorder{0}, mismatch{0};
  double latency_sum{0};
  double win_dt{0};
  bool seen{false};
  rosmax_common::LatencyHistogram hist;
};

}  // namespace

int main(int argc, char** argv) {
  rclcpp::init(argc, argv);
  const int gid = std::stoi(arg_of(argc, argv, "gid", "0"));
  const int n = std::stoi(arg_of(argc, argv, "n", "3"));
  const double rate = std::stod(arg_of(argc, argv, "rate_hz", "100"));
  const int size = std::stoi(arg_of(argc, argv, "payload_size", "1024"));
  const std::string qos_name = arg_of(argc, argv, "qos_profile", "reliable_volatile_depth10");
  const double ignore_sec = std::stod(arg_of(argc, argv, "ignore_sec", "2"));

  const auto logger = rclcpp::get_logger("mesh_g" + std::to_string(gid));
  if (n < 2 || n > 50 || rate <= 0 || size < static_cast<int>(HDR)) {
    RCLCPP_FATAL(logger, "非法参数 n=%d(2~50) rate=%f size=%d(≥%zu)", n, rate, size, HDR);
    return 1;
  }
  // 大拓扑统计间隔缩放：每秒输出行数 ≤ ~250（2450 话题 → 10s 一轮）
  const double stat_interval = std::max(1.0, std::ceil((static_cast<double>(n) * (n - 1)) / 250.0));

  auto qos = rosmax_common::qos_by_name(qos_name);

  // N 个逻辑节点（独立 ROS 节点名），共享进程与 participant
  std::vector<rclcpp::Node::SharedPtr> nodes;
  for (int j = 0; j < n; ++j) {
    nodes.push_back(rclcpp::Node::make_shared(
        "rosmax_g" + std::to_string(gid) + "_n" + std::to_string(j)));
  }

  auto topic_of = [gid](int from, int to) {
    return "/rosmax/g" + std::to_string(gid) + "/n" + std::to_string(from) +
           "_n" + std::to_string(to);
  };
  auto idx_of = [n](int from, int to) {
    return static_cast<size_t>(from) * (n - 1) + (to > from ? to - 1 : to);
  };

  String msg;
  msg.data.assign(size, 'x');

  std::vector<rclcpp::Publisher<String>::SharedPtr> pubs;  // 按 (j,k) 生成序
  std::vector<TopicStat> stats;                            // 同序
  stats.resize(static_cast<size_t>(n) * (n - 1));
  std::vector<rclcpp::Subscription<String>::SharedPtr> subs;

  for (int j = 0; j < n; ++j) {
    for (int k = 0; k < n; ++k) {
      if (k == j) continue;
      const size_t idx = idx_of(j, k);
      stats[idx].from = j;
      stats[idx].to = k;
      pubs.push_back(nodes[j]->create_publisher<String>(topic_of(j, k), qos));
      const int src = j, dst = k;
      subs.push_back(nodes[dst]->create_subscription<String>(
          topic_of(src, dst), qos,
          [&stats, idx, &logger](const String::SharedPtr m) {
            TopicStat& st = stats[idx];
            const uint64_t recv_ns = rosmax_common::now_ns();
            uint64_t seq, ts;
            if (!rosmax_common::string_decode_header(m->data, seq, ts)) {
              ++st.mismatch;
              return;
            }
            if (!st.seen) {
              st.seen = true;
              st.expected_seq = seq + 1;
              return;
            }
            if (seq > st.expected_seq) {
              st.lost += seq - st.expected_seq;
            } else if (seq < st.expected_seq) {
              ++st.disorder;
            }
            st.expected_seq = seq + 1;
            (void)logger;
            // 预热门控由主循环统一管理（见 warmed_up 写入点）
            const uint64_t lat = recv_ns - ts;
            st.hist.record(lat);
            st.latency_sum += lat;
            ++st.received;
            ++st.received_win;
          }));
    }
  }

  // 全局预热门控 + 每秒统计（独立线程访问 stats——executor 在专用线程 spin，
  // 回调与本线程无并发写同一字段？——有：回调写 received，本线程读。
  // 单字段 uint64 读写撕裂风险实际可忽略（64 位对齐读写在 aarch64 原子），
  // 且面板口径为趋势观察，非账务级精确。
  uint64_t start_ns = rosmax_common::now_ns();
  bool warmed_up = false;

  rclcpp::executors::SingleThreadedExecutor exec;
  for (auto& n : nodes) exec.add_node(n);
  std::thread spin_thread([&] { exec.spin(); });

  const auto period = std::chrono::duration_cast<std::chrono::steady_clock::duration>(
      std::chrono::duration<double>(1.0 / rate));
  auto next = std::chrono::steady_clock::now();
  auto stat_next = next + std::chrono::seconds(1);
  auto stat_t0 = stat_next;
  uint64_t seq{0}, missed{0};

  while (rclcpp::ok()) {
    // 统计块先行：不被节拍超期路径跳过（超期热旋时统计必须持续输出）
    if (std::chrono::steady_clock::now() >= stat_next) {
      if (!warmed_up) {
        // （预热门控）
        if (rosmax_common::now_ns() - start_ns >= static_cast<uint64_t>(ignore_sec * 1e9)) {
          warmed_up = true;
          start_ns = rosmax_common::now_ns();
          for (auto& s : stats) {
            s.received = 0;
            s.received_win = 0;  // 预热期计入的窗口计数一并清零（防首窗速率虚高）
            s.hist.reset();
          }
        }
      } else {
        for (auto& s : stats) {
          s.win_dt = stat_interval;
          const double wrate = s.received_win / s.win_dt;
          // fprintf 直写（绕过 rclcpp 日志与 rosout：2450 行/s 的 WARN 会放大成
          // 2450 msg/s 的 rosout 话题流量，把单进程压垮）
          fprintf(stdout,
                      "[STATS] to=%d from=%d recv=%llu rate=%.1fHz mean=%.0f p50=%.0f p99=%.0f p999=%.0f min=%llu max=%llu loss=%llu disorder=%llu mismatch=%llu\n",
                      s.to, s.from,
                      static_cast<unsigned long long>(s.received), wrate,
                      s.received ? s.latency_sum / static_cast<double>(s.received) : 0.0,
                      s.hist.percentile(50), s.hist.percentile(99), s.hist.percentile(99.9),
                      static_cast<unsigned long long>(s.hist.min()),
                      static_cast<unsigned long long>(s.hist.max()),
                      static_cast<unsigned long long>(s.lost),
                      static_cast<unsigned long long>(s.disorder),
                      static_cast<unsigned long long>(s.mismatch));
          s.received_win = 0;
          s.win_dt = 0;
        }
        fflush(stdout);
      }
      stat_t0 = stat_next;
      stat_next += std::chrono::duration_cast<std::chrono::steady_clock::duration>(
          std::chrono::duration<double>(stat_interval));
    }

    const uint64_t ts = rosmax_common::now_ns();
    rosmax_common::string_encode_header(msg.data, seq, ts);
    for (auto& p : pubs) p->publish(msg);
    ++seq;

    next += period;
    if (std::chrono::steady_clock::now() > next) {
      const auto skip = (std::chrono::steady_clock::now() - next) / period + 1;
      next += skip * period;
      missed += static_cast<uint64_t>(skip) + 1;
      // 超载保护：跳拍后必须睡到下一个未来节拍。不放任无 sleep 的最大速率
      // 发布——否则过载时（如 1×50@200Hz）会以极限速率持续轰炸整块板，
      // 把 sshd/监控通道一起压死（反复"界面空白"的根因）。
      if (std::chrono::steady_clock::now() < next) {
        std::this_thread::sleep_until(next);
      } else {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));  // 兜底间隔
      }
      continue;  // 统计块已在循环头部执行，不受影响
    }
    std::this_thread::sleep_until(next);
  }
  exec.cancel();
  spin_thread.join();

  if (missed > 0) {
    fprintf(stdout, "[PACER] missed=%llu of %llu beats (%.3f%%)\n",
            static_cast<unsigned long long>(missed),
            static_cast<unsigned long long>(seq),
            100.0 * missed / static_cast<double>(seq));
    fflush(stdout);
  }
  rclcpp::shutdown();
  return 0;
}
