// mesh_node — 组内全互联压测节点（网格模型，单来源统计口径 2026-10-27 用户定义）。
//
//   组 g 内 N 个节点，节点 j 向组内其他所有节点各发布一个话题：
//     发布: /rosmax/g<g>/n<j>_n<k>   (k≠j, 共 N-1 个)
//     订阅: /rosmax/g<g>/n<k>_n<j>   (k≠j, 共 N-1 个)
//   每组话题数 = N×(N-1)，恒大于节点数。话题名自带方向 (n0_n1)。
//
// 测量协议不变：String 前 42 字节文本头 <seq|ts>。
// 统计口径：**每来源独立**——直方图/计数/丢包按 (from→to) 分开跟踪，
// 每秒为每个来源打一行 [STATS] to=X from=Y ...，行率=该话题真实频率（=配置频率）。
#include <chrono>
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

class MeshNode : public rclcpp::Node {
 public:
  MeshNode() : Node("mesh_node") {
    gid_ = declare_parameter<int>("gid", 0);
    nidx_ = declare_parameter<int>("nidx", 0);
    gsize_ = declare_parameter<int>("gsize", 3);
    rate_hz_ = declare_parameter<double>("rate_hz", 100.0);
    payload_size_ = declare_parameter<int>("payload_size", 1024);
    qos_name_ = declare_parameter<std::string>("qos_profile", "reliable_volatile_depth10");
    ignore_sec_ = declare_parameter<double>("ignore_sec", 2.0);

    if (gsize_ < 2 || gsize_ > 10 || nidx_ < 0 || nidx_ >= gsize_ || rate_hz_ <= 0.0 ||
        payload_size_ < static_cast<int>(rosmax_common::STRING_HDR_LEN)) {
      RCLCPP_FATAL(get_logger(), "非法参数 gid=%d nidx=%d gsize=%d(2~10) rate=%f size=%d",
                   gid_, nidx_, gsize_, rate_hz_, payload_size_);
      throw std::invalid_argument("mesh params");
    }

    auto qos = rosmax_common::qos_by_name(qos_name_);

    // 每来源独立的统计状态（索引 = 对端节点号 k，跳过自己）
    for (int k = 0; k < gsize_; ++k) {
      if (k == nidx_) continue;
      pubs_.push_back(create_publisher<String>(topic_of(nidx_, k), qos));
      int src = k;
      subs_.push_back(create_subscription<String>(
          topic_of(src, nidx_), qos,
          [this, src](const String::SharedPtr m) { on_message(src, m); }));
      S st;
      st.peer = k;
      stats_.push_back(st);
    }
    start_ns_ = rosmax_common::now_ns();
    pacer_ = std::thread(&MeshNode::pacing_loop, this);
  }

  ~MeshNode() override {
    if (pacer_.joinable()) pacer_.join();
    for (auto& s : stats_) report(s, true);
  }

 private:
  struct S {
    int peer{0};
    uint64_t expected_seq{0}, received{0}, received_win{0}, lost{0}, disorder{0}, mismatch{0};
    double latency_sum{0};
    double win_dt{0};
    bool seen{false};
    rosmax_common::LatencyHistogram hist;
  };

  std::string topic_of(int from, int to) const {
    return "/rosmax/g" + std::to_string(gid_) + "/n" + std::to_string(from) +
           "_n" + std::to_string(to);
  }

  void on_message(int src, const String::SharedPtr& m) {
    const uint64_t recv_ns = rosmax_common::now_ns();
    S& s = stats_[src < nidx_ ? src : src - 1];  // stats 数组按对端号有序跳过自身
    uint64_t seq, ts;
    if (!rosmax_common::string_decode_header(m->data, seq, ts)) {
      ++s.mismatch;
      return;
    }
    if (!s.seen) {
      s.seen = true;
      s.expected_seq = seq + 1;
      return;
    }
    if (seq > s.expected_seq) {
      s.lost += seq - s.expected_seq;
    } else if (seq < s.expected_seq) {
      ++s.disorder;
    }
    s.expected_seq = seq + 1;
    if (!warmed_up_) {
      if ((recv_ns - start_ns_) >= static_cast<uint64_t>(ignore_sec_ * 1e9)) {
        warmed_up_ = true;
        start_ns_ = recv_ns;
        for (auto& st : stats_) {
          st.received = 0;
          st.hist.reset();
        }
      }
      return;
    }
    const uint64_t lat = recv_ns - ts;
    s.hist.record(lat);
    s.latency_sum += lat;
    ++s.received;
    ++s.received_win;
  }

  void report(S& s, bool final_) {
    const double rate = s.win_dt > 0 ? s.received_win / s.win_dt : 0.0;
    RCLCPP_WARN(get_logger(),
                "[STATS] to=%d from=%d recv=%llu rate=%.1fHz mean=%.0f p50=%.0f p99=%.0f p999=%.0f min=%llu max=%llu loss=%llu disorder=%llu mismatch=%llu%s",
                nidx_, s.peer,
                static_cast<unsigned long long>(s.received), rate,
                s.received ? s.latency_sum / static_cast<double>(s.received) : 0.0,
                s.hist.percentile(50), s.hist.percentile(99), s.hist.percentile(99.9),
                static_cast<unsigned long long>(s.hist.min()),
                static_cast<unsigned long long>(s.hist.max()),
                static_cast<unsigned long long>(s.lost),
                static_cast<unsigned long long>(s.disorder),
                static_cast<unsigned long long>(s.mismatch),
                final_ ? " final" : "");
    s.received_win = 0;
    s.win_dt = 0;
  }

  void pacing_loop() {
    using clock = std::chrono::steady_clock;
    const auto period = std::chrono::duration_cast<clock::duration>(
        std::chrono::duration<double>(1.0 / rate_hz_));
    auto next = clock::now();
    auto stat_next = clock::now() + std::chrono::seconds(1);
    auto stat_t0 = stat_next;
    uint64_t missed = 0;
    while (rclcpp::ok()) {
      const uint64_t ts = rosmax_common::now_ns();
      rosmax_common::string_encode_header(msg_.data, seq_, ts);
      for (auto& pub : pubs_) pub->publish(msg_);
      ++seq_;

      next += period;
      if (clock::now() > next) {
        const auto skip = (clock::now() - next) / period + 1;
        next += skip * period;
        missed += static_cast<uint64_t>(skip) + 1;
        continue;
      }
      if (clock::now() < stat_next) {
        std::this_thread::sleep_until(next);
        continue;
      }
      // 每来源一行 [STATS]（面板/采集器数据源）
      const double dt = std::chrono::duration<double>(clock::now() - stat_t0).count();
      if (warmed_up_) {
        for (auto& s : stats_) {
          s.win_dt = dt;
          report(s, false);
        }
      }
      stat_t0 = stat_next;
      stat_next += std::chrono::seconds(1);
      std::this_thread::sleep_until(next);
    }
    if (missed > 0) {
      RCLCPP_WARN(get_logger(), "[PACER] missed=%llu of %llu beats (%.3f%%)",
                  static_cast<unsigned long long>(missed),
                  static_cast<unsigned long long>(seq_),
                  100.0 * missed / static_cast<double>(seq_));
    }
  }

  int gid_, nidx_, gsize_;
  int payload_size_;
  double rate_hz_;
  std::string qos_name_;
  double ignore_sec_{2.0};
  uint64_t seq_{0};
  String msg_;
  std::vector<rclcpp::Publisher<String>::SharedPtr> pubs_;
  std::vector<rclcpp::Subscription<String>::SharedPtr> subs_;
  std::vector<S> stats_;
  bool warmed_up_{false};
  uint64_t start_ns_{0};
  std::thread pacer_;
};

int main(int argc, char** argv) {
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<MeshNode>());
  rclcpp::shutdown();
  return 0;
}
