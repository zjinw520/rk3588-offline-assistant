# 常见问题

| 现象 | 处理方式 |
| --- | --- |
| Runtime is not the verified official... | 按 MODELS.md 核对固定运行库 SHA-256，不修改校验值强行加载未知 ABI。 |
| libgomp.so.1 not found | 取得匹配板端架构和 glibc 的 GNU OpenMP 库，放入 vendor-local，再运行 doctor。 |
| GLIBC_2.38 not found，板子是 2.37 | 工具链系统库过新，取得兼容库，不覆盖系统 libc。 |
| 分词器缺文件 | 提供匹配模型的完整本地目录，核对路径与 tokenizer_config.json。 |
| 提示没有 PyTorch / TensorFlow / Flax | 仅使用分词器时可正常出现，实际计算由 RKLLM 完成，不必为消除提示安装它们。 |
| HTTP 409 metadata-only | 使用 --enable-inference 启动。 |
| HTTP 409 busy | 本版单任务，等当前请求结束或按 request_id 取消。 |
| Other RKLLM process / another runtime PID | 通过另一服务自己的控制方式停止它。端口不同也可能争用同一 NPU。 |
| Connection refused | 检查启动终端及端口，等服务就绪后查询 /healthz。 |
| 手机打不开 127.0.0.1:18080 | 这是手机自己；入门服务只监听板端回环地址。电脑可 SSH 转发，局域网网页另需 8081 组件。 |
| 根路径没有网页 | 模型服务只提供 API，用 tools/chat.py 调用；网页是单独组件。 |
| R1 慢或出现 think 标签 | CLI 返回模型原始流；R1 会生成长思考。先用 Qwen 验证交互。减小输出上限可能截断回答。 |
| 回答不准确 | 模型能力、提示词、识别输入和模板都会影响结果，换服务框架不改变模型能力。 |
| .onnx 是否也在 NPU 运行 | 本项目 SenseVoice、VITS 和 VAD 用 CPU；语言模型用 RKLLM/NPU。 |
| 不联网能用吗 | 文件准备齐全后本机不依赖互联网，手机仍需局域网；本次发布未重新进行物理断网验收。 |
| 升级驱动/换模型能直接用吗 | 驱动、Runtime、模型转换版本不同，保留回退并重新检查和推理，不能只凭名称保证兼容。 |
| 桌面没有图标 | ATK 的 SystemUI 使用 apk*.cfg 清单，普通 Desktop 下 .desktop 不一定生效，见应用布局。 |

反馈请附板型、RAM、内核、glibc/Python/RKNPU 版本、Runtime 校验值、模型来源和复现步骤；只贴相关错误，去掉私人对话和凭据。
