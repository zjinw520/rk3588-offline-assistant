# 新手从这里开始

适用基线：正点原子 ATK-DLRK3588 8GB、Buildroot、Linux 5.10、Qt 5.15 原厂桌面。参考板实际版本见 README。其他型号、Ubuntu 镜像或其他桌面，先完成文字服务，后面的声卡、SDK、应用入口需要适配。

这些教程提供从零搭建的明确步骤。模型服务及完整应用的既有部署已实测；新整理的整套空白系统安装步骤尚未在另一块全新板上走完，不能把命令核对或模拟测试当成全新板验收。

## 下载一次代码，外部文件按阶段准备

先在电脑下载 [packages 目录](../packages/README.md) 的 AArch64 入门包和 SHA256SUMS，再传入开发板。这个包已经包含本项目各阶段源码和模型适配库，不用逐个下载 Python/QML 文件。GitHub 的 Code → Download ZIP 也能取得源码，但没有 build 中的已编译适配库。

完整源码很小，模型、运行库、Python 包、Qt SDK 则分别准备。它们不在入门包内。不要把电脑的 x86 虚拟环境复制到 AArch64 开发板。

## 按顺序完成五个目标

| 阶段 | 教程 | 本阶段的成功标志 |
| --- | --- | --- |
| 1. 文字问答 | [QUICKSTART](QUICKSTART.md) | tools/chat.py 输入问题后得到正常结束的回答 |
| 2. 手机网页 | [WEB_SETUP](WEB_SETUP.md) | 手机访问板子 8081，输入文字得到回答 |
| 3. 开发板语音 | [VOICE_SETUP](VOICE_SETUP.md) | 录音可回放，能识别、问答、播报，唤醒提示音正常 |
| 4. 屏幕应用 | [DESKTOP_SETUP](DESKTOP_SETUP.md) | 原厂桌面单击打开、保持运行、正常退出并可再次打开 |
| 5. 桌面唤醒和开机启动 | [WAKE_AND_BOOT](WAKE_AND_BOOT.md) | 在原厂桌面说唤醒词打开应用；重启后服务恢复 |

第二个语言模型可在第一阶段后按 QUICKSTART 末尾添加，也可先只使用 Qwen。只有配置并验证过的模型才应被选中；本版原生界面仍显示两个固定模型选项，尚未安装 DeepSeek 时保持 Qwen。

## 命令究竟在哪执行

| 标注 | 含义 |
| --- | --- |
| Windows PowerShell / 电脑 | 下载、校验、SCP 上传；替换你自己的板卡 IP |
| 开发板终端 | SSH/串口登录后的 Linux 命令；后续安装按 root 用户描述 |
| Ubuntu SDK 主机 | 配套 SDK 所在的电脑或虚拟机，用它交叉编译 ARM Qt 程序 |

`/userdata/...` 是板子路径；`/home/.../board-sdk/...` 是 Ubuntu SDK 主机路径。`<板子IP>`、`/实际路径/...` 是占位符，不能原样执行。SSH 窗口里运行的命令仍是在开发板执行。

## 路径约定

新安装统一把入门包解压到 `/userdata/rk3588-offline-assistant`。本地模型和分词器放在 `/userdata/local-ai-files` 并写入 config.local.json。语音模型放 `/userdata/voice-models`，语音程序放 `/userdata/voice-assistant`。

部分实际运行路径仍有历史前缀，例如 `/userdata/rkllama-web.py`、`rkllama-qml`、`S99rkllama_wake`。它们是当前源码约定的文件名，教程保留它们以保证路径能对上，不要求安装其他模型服务框架。

**你已经有一个在用助手时，不要把“新安装”命令覆盖到在用目录。** 先保留备份、核对版本；你的既有部署不需要从第一步重装。历史部署迁移工具与空白系统搭建是两条路线，见 [BOARD_INSTALL](BOARD_INSTALL.md)。

## 先前台验证，再设置开机启动

Ctrl+C 停止前台服务；后台服务通过本项目控制工具停止。不要仅凭旧 PID 文件向进程发信号，也不要 killall python。重启会关闭手动启动的后台进程，直到第五阶段安装启动项前，需要重新启动模型和网页服务。

文件齐全后，本机识别、问答、播报不访问互联网。手机网页依赖手机与板子的局域网连接。最后按第五阶段做实际断外网检查，并记录哪些功能通过。

遇到问题先看当前阶段的检查表，不要跳到下一阶段。[故障记录](TROUBLESHOOTING_HISTORY.md) 解释本工程遇到过的真实问题和修复机制。
