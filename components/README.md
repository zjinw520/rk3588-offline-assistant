# 已运行应用的源码快照

这里收录当前开发板使用的网页、语音、原生界面源码，以及本地保存的原生宿主/桌面桥接构建源码。
它们来自本项目的逐步开发过程，不是从 RKLLama 仓库复制的服务实现。
当前导出的 Python 源码没有 import rkllama 或 from rkllama。
源码清单及 SHA-256 记录在 SOURCE_MANIFEST.json；不包含模型、录音、聊天日志或密码。

| 目录 | 内容 | 当前板端位置 |
| --- | --- | --- |
| voice | SenseVoice/VITS 调用、连续 PCM、VAD/唤醒、语音指令、桌面交接 | /userdata/voice-assistant |
| web | v0.8.1 网页与控制接口 | web_service.py 对应 /userdata/rkllama-web.py |
| native-ui | v0.8.0 QML、音量、对话状态、窗口监督、原厂入口 | /userdata/voice-assistant/rkllama-qml |
| native-ui/native-host | v0.4.3 Qt/C++ 宿主与 QtRemoteObjects 握手 | 编译为 rk3588-ai 后放到界面目录 |
| factory-bridge/preload | 原厂桌面启动桥接与隔离自检 | 仅在 SystemUI 进程加载，不替换原厂二进制 |

## 名称与依赖

为兼容已验证部署，部分变量、环境和目录保留 RKLLama 字样。
这些模块通过 HTTP 调用 127.0.0.1:8080；现在该端口由独立后端接管。
名称不表示它们正在导入或运行 RKLLama 框架。
requirements-board-web.txt 和 requirements-board-voice.txt 记录本板实际依赖，
不是面向所有系统的“最新推荐”。

Qt 原生宿主和桥接使用匹配板卡的 Qt 5.15 SDK；不包含 Qt 库、SDK、原厂 SystemUI 或模型。
在 Ubuntu SDK 主机编译，不在 Windows 或板端执行：

```sh
export RK3588_HOST_DIR=/path/to/board-sdk/buildroot/output/alientek_rk3588/host
sh components/native-ui/native-host/build.sh
sh components/factory-bridge/preload/build.sh
```

构建脚本检查 AArch64 和 GLIBC 版本；仍需在目标板运行 ldd 和隔离自检。
现有迁移路径已经实测，但空白系统一键安装尚未完成。
请按 [应用布局与复现边界](../docs/APPLICATION_LAYOUT.md) 核对目录、二进制和外部模型。
install_systemui.py 默认执行安装和备份，阅读源码并准备齐全后再运行。

项目自己的代码采用根目录 MIT。Qt、sherpa-onnx、模型等保持各自许可证，
详见 THIRD_PARTY.md；完整部署中的所有软件不因此变为原创或 MIT。
