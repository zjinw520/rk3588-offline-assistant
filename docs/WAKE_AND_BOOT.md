# 第五阶段：桌面唤醒、开机启动与离线验收

前提：第三阶段自动语音正常，第四阶段从原厂图标打开和退出均正常。第四阶段已上传 libsystemuiwake.so 和 systemui-wake-selftest。本章先接入原厂启动函数，再启用后台监听；不要先开多个录音程序。

## 1. 接入原厂桌面的启动流程

原生宿主负责应用打开后的状态通信；后台语音唤醒还需要让原厂桌面自己登记并启动助手。两者用途不同。

在开发板终端执行：

```sh
cd /userdata/rk3588-offline-assistant
python3 tools/patch_factory_startup.py
```

该工具只适用于已核对的 ATK 启动文件：

- 检查两个桥接构建产物的动态库依赖，运行隔离自测。
- 备份 /etc/init.d/S50systemui、保存原始/安装后哈希。
- 只为其中唯一一条直接启动 `/opt/ui/systemui` 的命令设置进程级 LD_PRELOAD 与启用标志，保留原重定向和后台符号。
- 不修改原厂 SystemUI 二进制，不给 sysvolume、终端或其他应用设置全局预加载。
- 启动文件不符合预期、已经改过或自测失败时停止，不能硬套到其他固件。

预期 `FACTORY_STARTUP_PATCHED_REBOOT_REQUIRED`，保存 Backup/Restore 输出。工具不会自动重启。新整理的这个安装辅助工具有自动测试；它本身还未在空白板上完整回归，首次安装应保留 SSH/串口恢复入口。

```sh
sync
reboot
```

重新登录后，若尚未装开机启动，先恢复模型和网页：

```sh
cd /userdata/rk3588-offline-assistant
export LOCAL_AI_PYTHON=/userdata/rk3588-offline-assistant/.venv/bin/python
python3 tools/service_control.py start
python3 tools/web_control.py start
```

检查桥接：

```sh
tail -n 40 /userdata/voice-assistant/systemui-wake-bridge.log
cd /userdata/voice-assistant
/userdata/voice-venv/bin/python factory_launch.py --check
```

预期日志有 BRIDGE_READY，检查返回 `FACTORY_LAUNCH_READY`。先不启用麦克风监听，直接验证原厂启动请求：

```sh
/userdata/voice-venv/bin/python /userdata/voice-assistant/factory_launch.py --launch
```

应打开助手并保持运行，再用界面正常退出。若失败，先检查本章末尾的恢复步骤，不反复直接运行原厂桌面程序。

## 2. 启动桌面后台唤醒

关闭助手窗口，网页里的语音应停止，手动录音程序已退出：

```sh
/userdata/voice-venv/bin/python /userdata/voice-assistant/desktop_wake.py --start
/userdata/voice-venv/bin/python /userdata/voice-assistant/desktop_wake.py --status
tail -n 40 /userdata/voice-assistant/desktop-wake.log
```

预期 `DESKTOP_WAKE_STARTED`，状态 mode=standby，日志有 DESKTOP_WAKE_READY。此时在原厂桌面说“你好小助手”，应经过：

```text
识别唤醒词
→ 本机 socket 请求原厂 callApp 启动入口
→ 原厂登记助手，并创建/激活应用
→ 原生窗口接管唤醒请求
→ 后台释放麦克风
→ 提示音后提问
```

说“结束对话”，助手停止语音并关闭窗口，返回原厂桌面；后台重新待唤醒。检查 DESKTOP_APP_LAUNCH_FACTORY、DESKTOP_WAKE_HANDOFF_OK，以及待唤醒/暂停状态切换。

麦克风在后台待唤醒和前台语音之间通过锁与交接状态共享，不应让两个录音进程同时占用硬件。桌面唤醒是运行 SenseVoice 的后台程序，不是断电后仍工作的独立硬件唤醒芯片。

停止后台监听：

```sh
/userdata/voice-venv/bin/python /userdata/voice-assistant/desktop_wake.py --stop
```

## 3. 安装新部署的基本开机服务

以下只给本教程从零创建的部署。已有在用启动项时不要重复安装。先停止后台待唤醒、关闭窗口和网页语音；模型/网页可由控制工具继续管理。

在开发板执行这个 Python 块，创建全新的启动文件；发现旧部署启动文件或目标已存在会拒绝覆盖：

```sh
python3 - <<'PY'
from pathlib import Path
target = Path('/etc/init.d/S98offline_assistant')
assert not target.exists() and not target.is_symlink(), '启动项已存在，未覆盖'
assert not Path('/etc/init.d/S99rkllama').exists(), '已有历史部署启动项，不能重复安装'
script = '''#!/bin/sh
APP_DIR=/userdata/rk3588-offline-assistant
export LOCAL_AI_PYTHON="$APP_DIR/.venv/bin/python"
case "$1" in
  start)
    mkdir -p /userdata/rkllama-lab || exit 1
    python3 "$APP_DIR/tools/service_control.py" start || exit 1
    python3 "$APP_DIR/tools/web_control.py" start || exit 1
    ;;
  stop)
    python3 "$APP_DIR/tools/web_control.py" stop || exit 1
    python3 "$APP_DIR/tools/service_control.py" stop || exit 1
    ;;
  status)
    result=0
    python3 "$APP_DIR/tools/service_control.py" status || result=1
    python3 "$APP_DIR/tools/web_control.py" status || result=1
    exit "$result"
    ;;
  restart) "$0" stop && "$0" start ;;
  *) echo "Usage: $0 {start|stop|restart|status}"; exit 1 ;;
esac
'''
with target.open('x', encoding='utf-8') as output:
    output.write(script)
target.chmod(0o755)
print('BASE_BOOT_FILE_READY')
PY
sh -n /etc/init.d/S98offline_assistant
sh /etc/init.d/S98offline_assistant start
sh /etc/init.d/S98offline_assistant status
```

启动已有的受管理服务会返回 already running，不新增重复进程。此启动文件用于本版 Buildroot 的 init.d 机制；其他系统应使用其对应服务管理方式。

## 4. 安装待唤醒启动项

桥接、手动桌面唤醒已经成功后才执行：

```sh
python3 - <<'PY'
from pathlib import Path
source = Path('/userdata/voice-assistant/S99rkllama_wake')
target = Path('/etc/init.d/S99rkllama_wake')
assert not target.exists() and not target.is_symlink(), '待唤醒启动项已存在，未覆盖'
with target.open('xb') as output:
    output.write(source.read_bytes())
target.chmod(0o755)
print('WAKE_BOOT_FILE_READY')
PY
sh -n /etc/init.d/S99rkllama_wake
sh /etc/init.d/S99rkllama_wake start
sh /etc/init.d/S99rkllama_wake status
```

init.d 按顺序启动：原厂桌面 → S98 基本模型/网页 → S99 待唤醒。正常重启，记录重启后的实际状态：

```sh
sync
reboot
```

重新登录后：

```sh
sh /etc/init.d/S98offline_assistant status
sh /etc/init.d/S99rkllama_wake status
tail -n 30 /userdata/voice-assistant/systemui-wake-bridge.log
```

同时验证手机文字、原厂图标、桌面喊醒、音量和退出。不能只检查 PID 存在就写“全部成功”。

## 5. 实际离线验收

先保持供电和本地模型文件完整。断开互联网连接，使用板子屏幕和自身麦克风/扬声器测试：

1. 点击图标，唤醒、提问、播报、退出。
2. 桌面喊醒，再次对话；验证 Qwen，已配置 DeepSeek 时再切换。
3. 调整音量、音色和语速。
4. 如果还需手机访问，保留局域网，只断路由器外网；关闭板子 WiFi 后手机原网络访问会中断。

模型/依赖已准备齐全时上述本机流程不需互联网。若通过 SSH 操作，关闭 WiFi 可能同时断开 SSH；使用板载屏幕或串口保留控制入口。本次教程整理没有远程替你执行物理断网验收。

## 6. 恢复与故障排查

先停止后台待唤醒，再停止窗口与基本服务：

```sh
sh /etc/init.d/S99rkllama_wake stop
sh /userdata/voice-assistant/rkllama-qml/run_qml_assistant.sh --stop
sh /etc/init.d/S98offline_assistant stop
```

某个组件没有运行时，定向停止可能提示 not running，按实际状态判断。停服务不会删除模型。

若只是取消开机启动，把**本教程创建的这两个启动文件**改成不以 S 开头的名字，保留其内容：

```sh
python3 - <<'PY'
from pathlib import Path
for name in ('S98offline_assistant', 'S99rkllama_wake'):
    source = Path('/etc/init.d') / name
    # Buildroot 有的 rcS 会遍历全部 S* 文件，因此禁用名也必须去掉首字母 S。
    target = source.with_name('disabled-' + name)
    assert not target.exists() and not target.is_symlink(), '已有禁用文件，先检查'
    if source.is_file() and not source.is_symlink():
        source.rename(target)
        print('DISABLED', target)
PY
```

恢复桥接启动命令，用第一节打印的备份路径：

```sh
cd /userdata/rk3588-offline-assistant
python3 tools/patch_factory_startup.py --rollback /userdata/voice-assistant/backups/factory-startup-实际编号
sync
reboot
```

目标启动脚本在安装后又被其他人修改时，工具拒绝直接覆盖，保留双方文件以便核对。图标回退使用第四阶段自己的 systemui-* 恢复点，两种备份不要混用。

| 现象 | 检查 |
| --- | --- |
| 没有 BRIDGE_READY | 看 bridge 日志及 S50systemui；检查 Qt 依赖与原厂 callApp 是否匹配 |
| FACTORY_LAUNCH_FAILED / socket 不存在 | 先修复桥接，不启动后台监听反复重试 |
| 检测到唤醒但窗口被桌面覆盖 | 检查是否走 DESKTOP_APP_LAUNCH_FACTORY，原生协议应答是否正常 |
| 待唤醒状态 retry/error | 看 desktop-wake.log、语音模型路径、声卡和麦克风锁 |
| 重启后网页打不开 | 先看 S98 status 和模型/网页日志，再核对板子是否连到另一保存的 WiFi |

历史问题和对应证据见 [TROUBLESHOOTING_HISTORY](TROUBLESHOOTING_HISTORY.md)。
