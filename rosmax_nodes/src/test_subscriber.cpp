// TestSubscriber — std_msgs/msg/String 版（公开标准类型，2026-10-27 用户要求）。
// callback 首行取时间，解析 42 字节文本头得 send_ts，统计口径与 STATS 格式不变。
// echo 模式（RTT 校准）原样回传收到的 String。
#include <chrono>
#include <memory>
#include <string>

#include <rclcpp/rclcpp.hpp>
#include <rosmax_common/latency_histogram.hpp>
#include <rosmax_common/qos_profiles.hpp>
#include <rosmax_common/string_protocol.hpp>
#include <rosmax_common/time_utils.hpp>
#include <std_msgs/msg/string.hpp>

using std_msgs::msg::String;

class TestSubscriber : public rclcpp::Node {
 public:
  TestSubscriber() : Node("test_subscriber") {
    topic_name_ = declare_parameter<std::string>("topic_name", "/test/topic_0");
    qos_name_ = declare_parameter<std::string>("qos_profile", "reliable_volatile_depth10");
    window_sec_ = declare_parameter<double>("window_size_sec", 1.0);
    ignore_sec_ = declare_parameter<double>("ignore_sec", 3.0);
    start_ns_ = rosmax_common::now_ns();

    sub_ = create_subscription<String>(
        topic_name_, rosmax_common::qos_by_name(qos_name_),
        [this](const String::SharedPtr m) { on_message(m); });
    enable_echo_ = declare_parameter<bool>("enable_echo", false);
    if (enable_echo_) {
      echo_topic_ = declare_parameter<std::string>("echo_topic", topic_name_ + "_echo");
      echo_pub_ = create_publisher<String>(echo_topic_, rosmax_common::qos_by_name(qos_name_));
    }
    stats_timer_ = create_wall_timer(std::chrono::duration<double>(window_sec_),
                                     std::bind(&TestSubscriber::report, this));
  }

 private:
  void on_message(const String::SharedPtr& m) {
    const uint64_t recv_ns = rosmax_common::now_ns();  // callback 首行
    uint64_t seq, ts;
    if (!rosmax_common::string_decode_header(m->data, seq, ts)) {
      ++mismatch_;
      return;
    }
    if (!seen_any_) {
      expected_seq_ = seq;
      seen_any_ = true;
    }
    if (!warmed_up_) {
      // 预热期：仅追踪 seq，不入任何统计（对齐 Apex --ignore 语义）
      expected_seq_ = seq + 1;
      if ((recv_ns - start_ns_) >= static_cast<uint64_t>(ignore_sec_ * 1e9)) {
        warmed_up_ = true;
        start_ns_ = recv_ns;
        window_start_ns_ = recv_ns;
        received_ = 0;
        received_window_ = 0;
        hist_.reset();
      }
      return;
    }
    if (seq > expected_seq_) {
      lost_ += seq - expected_seq_;
    } else if (seq < expected_seq_) {
      ++disorder_;
    }
    expected_seq_ = seq + 1;
    const uint64_t lat = recv_ns - ts;
    hist_.record(lat);
    latency_sum_ += lat;
    ++received_;
    ++received_window_;
    if (enable_echo_) {
      echo_pub_->publish(*m);
    }
  }

  void report() {
    const uint64_t now = rosmax_common::now_ns();
    const double dt = window_start_ns_ == 0 ? window_sec_ : (now - window_start_ns_) / 1e9;
    const double rate = received_window_ / dt;
    RCLCPP_INFO(get_logger(),
                "[STATS] recv=%llu rate=%.1fHz mean=%.0f p50=%.0f p99=%.0f p999=%.0f min=%llu max=%llu loss=%llu disorder=%llu mismatch=%llu",
                static_cast<unsigned long long>(received_),
                rate,
                received_ ? latency_sum_ / static_cast<double>(received_) : 0.0,
                hist_.percentile(50), hist_.percentile(99), hist_.percentile(99.9),
                static_cast<unsigned long long>(hist_.min()),
                static_cast<unsigned long long>(hist_.max()),
                static_cast<unsigned long long>(lost_),
                static_cast<unsigned long long>(disorder_),
                static_cast<unsigned long long>(mismatch_));
    window_start_ns_ = now;
    received_window_ = 0;
    hist_.reset();
  }

  std::string topic_name_, qos_name_, echo_topic_;
  double window_sec_, ignore_sec_{3.0};
  bool warmed_up_{false}, seen_any_{false}, enable_echo_{false};
  uint64_t start_ns_{0}, window_start_ns_{0};
  uint64_t received_{0}, received_window_{0}, lost_{0}, disorder_{0}, mismatch_{0};
  uint64_t expected_seq_{0};
  double latency_sum_{0};
  rosmax_common::LatencyHistogram hist_;
  rclcpp::Subscription<String>::SharedPtr sub_;
  rclcpp::Publisher<String>::SharedPtr echo_pub_;
  rclcpp::TimerBase::SharedPtr stats_timer_;
};

int main(int argc, char** argv) {
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<TestSubscriber>());
  rclcpp::shutdown();
  return 0;
}
