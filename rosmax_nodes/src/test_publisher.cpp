// TestPublisher — std_msgs/msg/String 版（公开标准类型，2026-10-27 用户要求）。
//
// 时延测量协议：String.data 前 42 字节为定长文本头 "<seq 20位>|<send_ts 20位>|"，
// 其余以 'x' 填充到目标字节数。任何 ROS2 工具可直接 echo 本话题。
// 时钟/节奏器语义不变：steady_clock 绝对节拍 + CLOCK_MONOTONIC_RAW 打点。
#include <chrono>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>

#include <rclcpp/rclcpp.hpp>
#include <rosmax_common/qos_profiles.hpp>
#include <rosmax_common/string_protocol.hpp>
#include <rosmax_common/time_utils.hpp>
#include <std_msgs/msg/string.hpp>

using std_msgs::msg::String;
using rosmax_common::STRING_HDR_LEN;
static constexpr size_t HDR_LEN = STRING_HDR_LEN;

class TestPublisher : public rclcpp::Node {
 public:
  TestPublisher() : Node("test_publisher") {
    topic_name_ = declare_parameter<std::string>("topic_name", "/test/topic_0");
    payload_size_ = declare_parameter<int>("payload_size", 1024);
    rate_hz_ = declare_parameter<double>("publish_rate_hz", 100.0);
    qos_name_ = declare_parameter<std::string>("qos_profile", "reliable_volatile_depth10");

    if (payload_size_ < HDR_LEN || rate_hz_ <= 0.0) {
      RCLCPP_FATAL(get_logger(), "非法参数 payload_size=%d(须≥%zu) rate=%f", payload_size_,
                   HDR_LEN, rate_hz_);
      throw std::invalid_argument("payload_size/rate_hz");
    }

    // 消息模板一次性构造，逐拍只改头部 42 字节（10MB 档避免每拍重分配）
    msg_.data.assign(payload_size_, 'x');
    rosmax_common::string_encode_header(msg_.data, 0, 0);

    pub_ = create_publisher<String>(topic_name_, rosmax_common::qos_by_name(qos_name_));

    enable_rtt_ = declare_parameter<bool>("enable_rtt", false);
    if (enable_rtt_) {
      rtt_topic_ = declare_parameter<std::string>("rtt_topic", topic_name_ + "_echo");
      rtt_sub_ = create_subscription<String>(
          rtt_topic_, rosmax_common::qos_by_name(qos_name_),
          [this](const String::SharedPtr m) {
            uint64_t seq, ts;
            if (!rosmax_common::string_decode_header(m->data, seq, ts)) return;
            std::lock_guard<std::mutex> lk(rtt_mtx_);
            auto it = inflight_.find(seq);
            if (it == inflight_.end()) return;
            const uint64_t rtt = rosmax_common::now_ns() - it->second;
            inflight_.erase(it);
            rtt_sum_ += rtt;
            ++rtt_n_;
            if (rtt_n_ == 1 || rtt < rtt_min_) rtt_min_ = rtt;
            if (rtt_n_ == 1 || rtt > rtt_max_) rtt_max_ = rtt;
          });
    }
    pacer_ = std::thread(&TestPublisher::pacing_loop, this);
  }

  ~TestPublisher() override {
    if (pacer_.joinable()) pacer_.join();
    if (rtt_n_ > 0) {
      RCLCPP_WARN(get_logger(), "[RTT] n=%llu mean=%.0f min=%llu max=%llu",
                  static_cast<unsigned long long>(rtt_n_),
                  rtt_sum_ / static_cast<double>(rtt_n_),
                  static_cast<unsigned long long>(rtt_min_),
                  static_cast<unsigned long long>(rtt_max_));
    }
  }

 private:
  void pacing_loop() {
    using clock = std::chrono::steady_clock;
    const auto period = std::chrono::duration_cast<clock::duration>(
        std::chrono::duration<double>(1.0 / rate_hz_));
    auto next = clock::now();
    uint64_t missed = 0;
    while (rclcpp::ok()) {
      const uint64_t ts = rosmax_common::now_ns();  // publish() 前最后一刻
      rosmax_common::string_encode_header(msg_.data, seq_, ts);
      if (enable_rtt_) {
        std::lock_guard<std::mutex> lk(rtt_mtx_);
        inflight_[seq_] = ts;
      }
      pub_->publish(msg_);
      ++seq_;

      next += period;
      if (clock::now() > next) {
        const auto skip = (clock::now() - next) / period + 1;
        next += skip * period;
        missed += static_cast<uint64_t>(skip) + 1;
        continue;
      }
      std::this_thread::sleep_until(next);
    }
    if (missed > 0) {
      RCLCPP_WARN(get_logger(), "[PACER] missed=%llu of %llu (%.3f%%)",
                  static_cast<unsigned long long>(missed),
                  static_cast<unsigned long long>(seq_),
                  100.0 * missed / static_cast<double>(seq_));
    }
  }

  std::string topic_name_, qos_name_, rtt_topic_;
  int payload_size_;
  double rate_hz_;
  uint64_t seq_{0};
  String msg_;
  rclcpp::Publisher<String>::SharedPtr pub_;
  rclcpp::Subscription<String>::SharedPtr rtt_sub_;
  bool enable_rtt_{false};
  std::mutex rtt_mtx_;
  std::unordered_map<uint64_t, uint64_t> inflight_;
  double rtt_sum_{0};
  uint64_t rtt_n_{0}, rtt_min_{0}, rtt_max_{0};
  std::thread pacer_;
};

int main(int argc, char** argv) {
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<TestPublisher>());
  rclcpp::shutdown();
  return 0;
}
