# 应用布局与复现边界

目标是已验证的 ATK-DLRK3588 Buildroot 桌面镜像，不是通用 Linux 桌面发行版。
基线为 8GB、Linux 5.10.209、RKNPU 0.9.8、glibc 2.37、Python 3.10.5、Qt 5.15 系列。
文字模型使用 RKLLM 1.3.0，SenseVoice/VITS 使用 CPU 上的 sherpa-onnx。

## 外部资产

以下内容不随源码包分发，需从各自上游取得并遵守对应许可证。

| 资产 | 本板位置 | 用途 |
| --- | --- | --- |
| RKLLM Runtime 1.3.0 与兼容 libgomp.so.1 | vendor-local | NPU 推理与 OpenMP 依赖 |
| Qwen2-0.5B W8A8 RK3588 模型和本地分词器 | /userdata/rkllama-models/qwen2-0.5b | 快速问答 |
| DeepSeek-R1-Distill-Qwen-1.5B W8A8 和分词器 | /userdata/rkllama-models/deepseek-r1-1.5b | 带思考的问答 |
| sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17 | /userdata/voice-models/同名目录 | model.int8.onnx、tokens.txt，离线识别 |
| sherpa-onnx-vits-zh-ll | /userdata/voice-models/同名目录 | model.onnx、lexicon.txt、tokens.txt、phone/date/number.fst，中文合成 |
| silero_vad.onnx | /userdata/voice-models/silero_vad.onnx | 语音起止检测 |
| 原厂 SystemUI、Qt/Wayland、ALSA | 固件提供 | 屏幕、入口、声卡、按键 |

转换工具、运行库、驱动与模型格式需要匹配；文件名相同不代表兼容。
运行库来源与固定指纹见 THIRD_PARTY.md。
分词器必须对应语言模型。这里常被称作“提词器”的 tokenizer，
负责文字和 token 编号转换及聊天模板，不是另一个神经网络或提词屏。

## 当前安装映射

```text
/userdata/local-ai-candidate-v010/
  local_ai/                    独立模型服务
  tools/service_control.py     start/stop/status
  tools/trial_board.py          进程身份与隔离试验；管理器也会复用它
  run_candidate.sh             离线和动态库环境
  config.local.json            本机模型路径
  build/liblocalai-rkllm130.so  本项目适配层
  vendor-local/                官方库的本机副本
  run/                        本机进程身份，不跨机器恢复
  backups/                    启动脚本恢复点

/userdata/voice-assistant/
  components/voice 中的运行文件
  rkllama-qml/
    components/native-ui 中的运行文件
    rk3588-ai                  native-host 构建的宿主
    rk3588-ai.png              图标，随源码提供 SVG 原稿
  libsystemuiwake.so           factory-bridge/preload 的构建产物

/userdata/rkllama-web.py        components/web/web_service.py 的部署名称
/userdata/rkllama-venv/         分词器、Flask；名称沿用，不要求 RKLLama 包
/userdata/voice-venv/           sherpa-onnx、NumPy
```

## 迁移与从零部署

已有部署按 BOARD_FIRST_CHECK → BOARD_NPU_TRIAL → BOARD_INSTALL 操作。
这条路径已经实测，并保留原有界面、音频、原厂桌面和恢复点。

空白镜像一键安装尚未完成实机回归。手动搭建时按以下顺序逐层验证：

1. 确认声卡、屏幕、Wayland、音量键与 NPU 驱动。
2. 准备本地模型、分词器和两个普通 Python 环境，只安装所需依赖，不安装 RKLLama 包。
3. 编译适配层并通过 doctor、隔离问答。现有迁移安装器要求旧 S99rkllama，不能直接用于空白系统。
4. 按上表映射语音与网页源码，先前台验证 8080/8081，再设置服务。
5. 在匹配 SDK 编译原生宿主和桥接，在板端检查 ldd 与隔离自检。
6. 从提供的 SVG 准备 PNG 图标，再安装原厂入口。
7. 仅对 SystemUI 启动命令加载 libsystemuiwake.so 并设置 RK3588_SYSTEMUI_WAKE_BRIDGE=1，
   不把 LD_PRELOAD 写入全局环境，不替换原厂二进制。
8. 最后核对唤醒交接、模型选择、对话、退出、音量同步、重启和回退。

语音启动脚本暂时沿用 RKLLAMA_LIB 依赖目录变量。
新安装可把它指向本项目 vendor-local，并核对 desktop_wake.py 的 LIB。
目录名称是兼容约定，不要求安装旧框架。
路径清理和全新系统安装自动化属于后续发行工作，本版不将其标记为完成。
