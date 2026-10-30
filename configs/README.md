# configs/

- MVP（W2）：T1-01/T1-02 用例 YAML + transport 强制的 FastDDS/CycloneDDS XML
- W3/W4（延后）：T1-03~06/08/10 用例集、Discovery Server / 静态发现配置模板

transport 取值：`udp | shm | default`（不提供 zero_copy——无界数组与 Fast DDS data-sharing 不兼容）
