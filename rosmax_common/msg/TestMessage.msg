# 压测消息：时间戳在 publish() 调用前写入，订阅端 callback 首行读取（CLOCK_MONOTONIC_RAW 纳秒）
uint64 seq            # 发布者内单调递增
uint64 send_ts_ns     # 发布时刻（CLOCK_MONOTONIC_RAW）
uint32 node_id        # 发布者节点编号
uint32 topic_id       # topic 编号
uint32 payload_len    # 实际 payload 字节数，须与 payload.size() 一致，不一致计入错误
uint8[] payload       # 按配置填充的无界数组（64B ~ 10MB 档位共用此类型）
