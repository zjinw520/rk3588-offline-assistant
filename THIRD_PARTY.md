# 代码与依赖的来源

本文件用于说明工程来源和发布边界。它不替代各组件自己的许可证。

| 部分 | 来源与本版处理 |
| --- | --- |
| local_ai、native/bridge.cpp、native/compat、tools、tests | 本次独立编写；MIT |
| RKLLM 1.3.0 头文件 | 官方原文随源码附带；保留单独许可证 |
| RKLLM Runtime | 用户已经持有的官方二进制；本项目发布包不附带；准备工具核对并复制到本机 vendor-local |
| libgomp.so.1 | GNU OpenMP 运行库；按其自己的许可证及运行库例外处理；本版不向外发布 |
| Transformers | 外部依赖；Apache-2.0；只用来离线加载分词器及模板 |
| Flask、NumPy 等 Python 包 | 外部运行依赖，不附带包代码；实际版本记录在 components/requirements-board-*.txt，各自许可证不由本项目修改 |
| Qwen2 / DeepSeek 模型和分词器 | 用户现有外部模型文件；不纳入源码或发布包，分别遵守原始模型、蒸馏模型及转换产物的条款 |
| RKLLama | 现有部署中的模型服务；GPL-3.0；本版没有附带、修改或导入它的 Python 源码 |
| SenseVoice、VITS、Silero | 外部模型；不附带权重，分别遵守原模型及转换产物的授权 |
| sherpa-onnx | 外部运行依赖；[Apache-2.0](https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE)，本包不附带库 |
| Qt / QtRemoteObjects / ALSA | 外部系统依赖；不附带库或 SDK。Qt 5.15 RemoteObjects 上游头文件声明 LGPLv3/GPL 等授权选择，见[上游声明](https://github.com/qt/qtremoteobjects/blob/v5.15.2/src/remoteobjects/qremoteobjectnode.h)；实际分发须按所用版本及模块处理 |
| 正点原子 SystemUI | 原厂环境的一部分；不发布其二进制，不把它列为原创 |

components 中收录本项目已运行的应用源码快照和本地原生构建源码，不附带上述外部组件。
Python 导入检查未发现 rkllama 包依赖。部分兼容目录及日志名称仍沿用 RKLLama，
不能因此推断有旧框架源文件，也不能据此省略第三方库自己的许可要求。

### 固定的官方 SDK 来源

- 仓库：[airockchip/rknn-llm](https://github.com/airockchip/rknn-llm)。
- 1.3.0 固定提交：878f9361fd3afa7e167b7079918918f78d2c1c2a。
- [头文件原文](https://github.com/airockchip/rknn-llm/blob/878f9361fd3afa7e167b7079918918f78d2c1c2a/rkllm-runtime/Linux/librkllm_api/include/rkllm.h)。
- [许可证原文](https://github.com/airockchip/rknn-llm/blob/878f9361fd3afa7e167b7079918918f78d2c1c2a/LICENSE)。
- 头文件 Git blob SHA-1：c76346c7f7bcab9e8eb61e3f903ee93cdc03d236。
- 官方 aarch64/librkllmrt.so 大小：7,617,472 字节。
- 该运行库 Git blob SHA-1：34167b32f99cb7ab19e21fb579fb750d96b878f1。

Git blob 哈希包含 blob 头部，并非直接对文件运行 sha1sum 的结果。
本项目另计算 SHA-256 供板端记录；SHA-1 blob 主要用于与上游 Git 对象对应。
用户原 RKLLama 固定提交中的同名运行库，与上述官方文件的 Git blob 和大小一致；
它本身的来源是 Rockchip，不是 RKLLama 自研的推理核心。

### 如何描述这个项目

可以说“自主开发的 RK3588 离线语音助手，使用 Rockchip 推理运行库和开源模型”。
不能说“语言模型、语音识别、语音合成、系统桌面和所有底层库都是原创”。
删除项目名称、换 UI、重新打包，都不会改变已有代码和依赖的版权来源。
若后续仍需复制或修改 RKLLama 的 GPL 源码，应保留来源并按相应许可证发布，
不能直接放进本项目 MIT 代码范围。
