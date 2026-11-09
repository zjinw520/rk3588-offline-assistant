# v0.1.0 Linux AArch64 入门包

下载本目录的 rk3588-offline-assistant-v0.1.0-aarch64.tar.gz，使用 SHA256SUMS 校验，然后按 docs/START_HERE.md 的路线操作。

它包含当前源码及本项目生成的 build/liblocalai-rkllm130.so（Linux AArch64，glibc 2.17，C ABI 1），附逐文件 PACKAGE_MANIFEST.json；不含 Rockchip Runtime、模型权重、分词器、libgomp、Qt 或 Python 环境。

这是源码仓库内的入门包，方便没有板端编译器的用户下载，不是完整固件或空白系统的一键安装镜像。内部源码以包内清单为准，后续仓库提交可能更新文档。

本包现已附网页、语音、原生桌面、待唤醒/开机启动教程和实际故障修复记录。安装步骤区分电脑、开发板和 Ubuntu SDK 主机，仍需按目标板逐层验收。
