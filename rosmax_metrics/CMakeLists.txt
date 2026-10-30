cmake_minimum_required(VERSION 3.16)
project(rosmax_metrics)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)

# W2 交付：metrics_collector 可执行文件
# /proc/<pid>/stat utime+stime 增量 / 窗口墙钟 = 单核百分比（分子分母都进报告）
# 只监视测试进程 PID 列表（参数传入），避免 100ms 全表扫描的采集开销
ament_package()
