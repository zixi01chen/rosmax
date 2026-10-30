// topic_lister — 设备话题发现：每秒输出本域内全部话题（含外部系统建的）及 pub/sub 数。
// 输出: [TOPICS] {"topics":[{"name":..,"type":..,"pubs":N,"subs":N},...]}
// .23 无 ros2 CLI，本节点用 rclcpp graph API 自列。
#include <chrono>
#include <cstdio>
#include <string>
#include <thread>

#include <rclcpp/rclcpp.hpp>

using namespace std::chrono_literals;

int main(int argc, char** argv) {
  rclcpp::init(argc, argv);
  auto node = rclcpp::Node::make_shared("topic_lister");
  while (rclcpp::ok()) {
    try {
      auto map = node->get_topic_names_and_types();
      printf("[TOPICS] {\"topics\":[");
      bool first = true;
      for (const auto& [name, types] : map) {
        if (name == "/parameter_events" || name == "/rosout") continue;
        size_t pubs = 0, subs = 0;
        try {
          pubs = static_cast<size_t>(node->count_publishers(name));
          subs = static_cast<size_t>(node->count_subscribers(name));
        } catch (...) {}
        std::string type = types.empty() ? "" : types[0];
        printf("%s{\"name\":\"%s\",\"type\":\"%s\",\"pubs\":%zu,\"subs\":%zu}",
               first ? "" : ",", name.c_str(), type.c_str(), pubs, subs);
        first = false;
      }
      printf("]}\n");
      fflush(stdout);
    } catch (const std::exception& e) {
      printf("[TOPICS] {\"error\":\"%s\"}\n", e.what());
      fflush(stdout);
    }
    std::this_thread::sleep_for(1s);
  }
  rclcpp::shutdown();
  return 0;
}
