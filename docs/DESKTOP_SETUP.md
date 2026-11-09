# 第四阶段：原厂桌面上的原生助手

前提：第二、三阶段已通过；目标是 ATK-DLRK3588 的 `/opt/ui/systemui`、Qt 5.15、Weston/Wayland 固件。需要配套 Ubuntu SDK 主机交叉编译。没有匹配 SDK 或原厂桌面的板子，先使用网页和终端语音，不套用本章入口配置。

本章把自研程序加入原厂应用清单，并实现原厂桌面要求的状态通信。没有替换原厂 SystemUI 二进制，也不意味着助手变成厂商提供的软件。

## 1. 在开发板检查环境

```sh
ps -ef | grep '[s]ystemui'
ps -ef | grep '[w]eston'
ls -l /var/run/wayland-0
cat /opt/ui/src/ATK-DLRK3588/apk1.cfg
```

预期只有一个 SystemUI 进程、有 Wayland socket，应用清单中可见相机/文件等原厂应用。普通 Linux 桌面的 Desktop 目录和 .desktop 文件不负责这个原厂应用网格。

复制原生界面源码到语音程序旁边，目标已存在会停止：

```sh
python3 - <<'PY'
from pathlib import Path
import shutil
source = Path('/userdata/rk3588-offline-assistant/components/native-ui')
target = Path('/userdata/voice-assistant/rkllama-qml')
assert target.parent.is_dir(), '先完成语音程序部署'
assert not target.exists() and not target.is_symlink(), '已有界面目录，未覆盖'
shutil.copytree(source, target)
for name in ('run_qml_assistant.sh', 'systemui_entry.sh'):
    (target / name).chmod(0o755)
print('NATIVE_UI_FILES_READY')
PY
```

## 2. 在 Ubuntu SDK 主机编译

**这一节在 Ubuntu 电脑/虚拟机执行，不在开发板执行。** 把同一版完整入门包或仓库源码放到 SDK 主机。下面示例假设源码在 `$HOME/rk3588-offline-assistant`；SDK 路径要替换成你自己的实际目录。

```sh
cd "$HOME/rk3588-offline-assistant"
export RK3588_HOST_DIR=/实际路径/board-sdk/buildroot/output/alientek_rk3588/host
export RK3588_BUILD_JOBS=2
sh components/native-ui/native-host/build.sh
sh components/factory-bridge/preload/build.sh
```

应生成：

| 主机产物 | 后续用途 |
| --- | --- |
| components/native-ui/native-host/build/rk3588-ai | 原生窗口宿主，包含原厂桌面状态协议 |
| components/factory-bridge/preload/dist/libsystemuiwake.so | 第五阶段让待唤醒服务走原厂启动函数 |
| components/factory-bridge/preload/dist/systemui-wake-selftest | 桥接的隔离自测 |

需要 SDK 的 AArch64 gcc/g++、Qt 5.15 qmake、Qt Quick/QML/Gui/Network/RemoteObjects 开发文件。qmake 缺失可设置 RK3588_QMAKE 指向 SDK 内实际 qmake，不要使用 Ubuntu 的 x86 Qt 编译器冒充。

构建脚本检查目标架构和最高 GLIBC 符号需求。参考板 glibc 为 2.37；SDK 过新时构建会拒绝部署，需要匹配的 sysroot，不能覆盖板端 libc。

把仓库 SVG 图标转换成 PNG。以下安装命令也只在 Ubuntu SDK 主机执行；已有 rsvg-convert 可跳过安装：

```sh
sudo apt-get update
sudo apt-get install librsvg2-bin
rsvg-convert -w 128 -h 128 -o /tmp/rk3588-ai.png components/native-ui/rk3588-ai-assistant.svg
file /tmp/rk3588-ai.png
```

## 3. 从 SDK 主机上传构建产物

目标目录由第一节创建。使用自己的板卡 IP；本节只针对目标原生二进制尚不存在的新安装。

```sh
scp components/native-ui/native-host/build/rk3588-ai root@<板子IP>:/userdata/voice-assistant/rkllama-qml/rk3588-ai.new
scp /tmp/rk3588-ai.png root@<板子IP>:/userdata/voice-assistant/rkllama-qml/rk3588-ai.png
scp components/factory-bridge/preload/dist/libsystemuiwake.so root@<板子IP>:/userdata/voice-assistant/libsystemuiwake.so
scp components/factory-bridge/preload/dist/systemui-wake-selftest root@<板子IP>:/userdata/voice-assistant/systemui-wake-selftest
```

编译完成不表示板端能加载。回到**开发板终端**：

```sh
cd /userdata/voice-assistant/rkllama-qml
chmod 755 rk3588-ai.new
ldd ./rk3588-ai.new
./rk3588-ai.new --self-test-systemui
ldd /userdata/voice-assistant/libsystemuiwake.so
chmod 755 /userdata/voice-assistant/systemui-wake-selftest
/userdata/voice-assistant/systemui-wake-selftest
```

所有 ldd 依赖应可找到，没有 GLIBC/GLIBCXX 版本错误。预期 `SYSTEMUI_SELFTEST_OK v0.4.3` 和 `SYSTEMUI_WAKE_SELFTEST_OK v1.1`。这是隔离测试，不会打开真正的助手窗口或启动桌面唤醒。

确认通过后启用新二进制。目标已存在时不覆盖：

```sh
python3 - <<'PY'
from pathlib import Path
folder = Path('/userdata/voice-assistant/rkllama-qml')
target = folder / 'rk3588-ai'
assert not target.exists() and not target.is_symlink(), '已有二进制，请先备份检查'
(folder / 'rk3588-ai.new').rename(target)
print('NATIVE_HOST_READY_TO_INSTALL')
PY
sh /userdata/voice-assistant/rkllama-qml/run_qml_assistant.sh --check --from-systemui
/userdata/voice-assistant/rkllama-qml/rk3588-ai --inspect-systemui
```

--check 检查文件和 Wayland；--inspect-systemui 只读取原厂接口元数据，不登记应用状态。参考固件应有 `serverSendVariant(QString,int,int,QVariant)` 与 `onServerVariant(QString,int,int,QVariant)`。协议不同时停止本章，不通过盲目重试/改二进制名解决。

## 4. 注册原厂应用图标

先停止网页中的语音对话并关闭助手窗口，确认 8080/8081 正常，然后执行：

```sh
python3 /userdata/voice-assistant/rkllama-qml/install_systemui.py
```

该工具会备份配置，把入口安装到 `/opt/ui/src/apps/rk3588-ai`，安装 PNG，并在 ATK-DLRK3588 的 apk1.cfg 留一个助手条目。它也会把明确列出的旧浏览器入口移到备份，保留原厂其他应用。记录输出中的 Backup 和 Restore command，别随意删除 backups。

预期 `Native assistant installed once on SystemUI page one.`。然后正常重启，让原厂桌面重新加载应用清单：

```sh
sync
reboot
```

第五阶段尚未安装开机启动时，重启后重新 SSH 登录，启动模型和网页：

```sh
cd /userdata/rk3588-offline-assistant
export LOCAL_AI_PYTHON=/userdata/rk3588-offline-assistant/.venv/bin/python
python3 tools/service_control.py start
python3 tools/web_control.py start
```

## 5. 在屏幕上验收

1. 在相机、文件等应用所在的原厂桌面单击“离线AI助手”。
2. 等到本地服务就绪，保持窗口至少一分钟；打开设置再返回。
3. 选择已安装的 Qwen，开始语音，按第三阶段方法提问。
4. 测试音量按钮和实体 V+/V−，核对原厂音量文件和屏幕一致。
5. 点“退出”并确认，返回原厂桌面；再次单击图标，应能重新打开。

不能因为进程存在就判定窗口显示正常，也不能因为失去焦点就判定进程崩溃。原厂桌面按应用清单、进程名和状态通信管理窗口。入口脚本必须前台 exec 宿主，不能加 nohup、& 或立即退出，否则父 QProcess 的生命周期不一致。

日志检查：

```sh
tail -n 80 /userdata/voice-assistant/qml-assistant.log
cat /userdata/voice-assistant/qml-assistant-state.json
ps -ef | grep '[r]k3588-ai'
```

正常打开可见 APP_READY、NATIVE_HOST_READY、SYSTEMUI_CONNECTED 和状态应答；正常退出可见 APP_EXIT_REQUEST/APP_EXIT_DONE、SYSTEMUI_RETURN_HOME、WINDOW_EXIT code=0。状态文件在窗口退出后不存在是正常现象。

| 现象 | 处理 |
| --- | --- |
| 图标不见 | 核对实际桌面是否 SystemUI、apk1.cfg、PNG 路径，再正常重启；复制 .desktop 不会更新这个应用网格 |
| 两秒后像关闭、后台仍在 | 核对原厂启动登记、原生协议、入口是否被后台化，见故障记录；不要先归因于触屏 lag 日志 |
| 快速点多次后闪退 | 先停止重复启动，核对入口和单实例锁；保持一个 SystemUI、一个窗口监督进程 |
| 无法退出 | 先用界面确认退出；SSH 可执行下面的定向停止命令 |

```sh
sh /userdata/voice-assistant/rkllama-qml/run_qml_assistant.sh --stop
```

恢复图标配置时使用安装器打印的确切路径：

```sh
python3 /userdata/voice-assistant/rkllama-qml/install_systemui.py --rollback /userdata/voice-assistant/backups/systemui-实际编号
sync
reboot
```

回退前关闭助手，避免窗口仍持有安装锁。不要停止 Weston 或再次运行原厂 S50systemui start 来“刷新”图标，这可能额外启动 sysvolume。第五阶段：[桌面唤醒与开机启动](WAKE_AND_BOOT.md)。
