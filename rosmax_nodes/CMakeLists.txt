cmake_minimum_required(VERSION 3.16)
project(rosmax_nodes)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(std_msgs REQUIRED)
find_package(rosmax_common REQUIRED)

add_executable(test_publisher src/test_publisher.cpp)
ament_target_dependencies(test_publisher rclcpp rosmax_common std_msgs)

add_executable(test_subscriber src/test_subscriber.cpp)
ament_target_dependencies(test_subscriber rclcpp rosmax_common std_msgs)

add_executable(topic_lister src/topic_lister.cpp)
ament_target_dependencies(topic_lister rclcpp std_msgs)

add_executable(mesh_group src/mesh_group.cpp)
ament_target_dependencies(mesh_group rclcpp rosmax_common std_msgs)
target_include_directories(mesh_group PRIVATE ${rosmax_common_INCLUDE_DIRS})

# 注：ament_target_dependencies 优先用导出 target（rosidl typesupport target 只携带
# include/rosmax_common 一层），header-only 头位于 include/ 根，需显式补全变量里的目录。
foreach(tgt test_publisher test_subscriber)
  target_include_directories(${tgt} PRIVATE ${rosmax_common_INCLUDE_DIRS})
endforeach()

install(TARGETS test_publisher test_subscriber topic_lister mesh_group DESTINATION lib/${PROJECT_NAME})

ament_package()
