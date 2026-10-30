#pragma once
// std_msgs/msg/String 压测协议：data 前 42 字节为定长文本头 "<seq 20位>|<send_ts 20位>|"，
// 其余 'x' 填充。任何 ROS2 工具可直接 echo 本话题；seq/ts 为十进制定宽，解析零分配。
#include <cstdint>
#include <cstdlib>
#include <string>

namespace rosmax_common {

constexpr size_t STRING_HDR_LEN = 42;  // 20(seq) + '|' + 20(ts) + '|'

inline void string_encode_header(std::string& s, uint64_t seq, uint64_t ts) {
  char buf[STRING_HDR_LEN + 1];
  int n = snprintf(buf, sizeof(buf), "%020lu|%020lu|",
                   static_cast<unsigned long>(seq), static_cast<unsigned long>(ts));
  if (n < 0 || static_cast<size_t>(n) < STRING_HDR_LEN) return;
  s.replace(0, STRING_HDR_LEN, buf, STRING_HDR_LEN);
}

inline bool string_decode_header(const std::string& s, uint64_t& seq, uint64_t& ts) {
  if (s.size() < STRING_HDR_LEN || s[20] != '|' || s[41] != '|') return false;
  seq = strtoull(s.substr(0, 20).c_str(), nullptr, 10);
  ts = strtoull(s.substr(21, 20).c_str(), nullptr, 10);
  return ts != 0;
}

}  // namespace rosmax_common
