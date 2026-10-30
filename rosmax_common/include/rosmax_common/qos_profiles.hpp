#pragma once
// QoS 配置注册表：按名字取 profile，配置文件里写名字而不是手拼 QoS 结构。
// W2 扩展：best_effort 系列、depth 可参数化；transport 强制走 DDS XML，不经 QoS。
#include <rclcpp/qos.hpp>
#include <string>

namespace rosmax_common {

inline rclcpp::QoS qos_by_name(const std::string& name) {
  // 基线：reliable + volatile + depth10（设计文档基线 QoS）
  if (name == "reliable_volatile_depth10") {
    rclcpp::QoS qos(rclcpp::KeepLast(10));
    qos.reliable().durability_volatile();
    return qos;
  }
  // T1-02 吞吐口径：best_effort 下丢包才可见
  if (name == "best_effort_volatile_depth10") {
    rclcpp::QoS qos(rclcpp::KeepLast(10));
    qos.best_effort().durability_volatile();
    return qos;
  }
  // 未知名字：fail loud（设计原则：配置错误不该被静默降级）
  throw std::invalid_argument("unknown qos_profile: " + name);
}

}  // namespace rosmax_common
