# 第二阶段：手机网页问答

前提：第一阶段 Qwen 已真实回答过问题。本章默认入门包目录 `/userdata/rk3588-offline-assistant`，环境 `.venv`，全程在开发板终端操作，手机只用于最后访问页面。没有语音模型也能先完成本章。

## 1. 将模型服务切换到应用约定的 8080

第一阶段用 18080 做独立测试；后续网页和语音源码固定连接本机 8080。先回到运行 18080 服务的窗口，Ctrl+C 正常停止它，再执行：

```sh
cd /userdata/rk3588-offline-assistant
export LOCAL_AI_PYTHON=/userdata/rk3588-offline-assistant/.venv/bin/python
mkdir -p /userdata/rkllama-lab
python3 tools/service_control.py start
python3 tools/service_control.py status
```

预期 `LOCAL_AI_STARTED ... port=8080` 和 `LOCAL_AI_RUNNING`。历史兼容 PID 文件写在 rkllama-lab；这个空目录本身不包含其他框架。

```sh
python3 - <<'PY'
import json
from urllib.request import urlopen
with urlopen('http://127.0.0.1:8080/healthz', timeout=5) as response:
    status = json.load(response)
assert status['backend'] == 'local-ai-independent'
assert status['inference_enabled'] is True
print('MODEL_API_OK')
PY
python3 tools/chat.py --url http://127.0.0.1:8080 --model qwen2-0.5b --question '你好，请用一句话回答。'
```

端口检查通过和模型推理成功是两个检查。若提示别的进程使用运行库，停止那个服务自己的进程后再试；不要同时保留两个推理服务。

## 2. 安装网页依赖并复制网页源码

```sh
. .venv/bin/activate
python -m pip install -r components/requirements-board-web.txt
python -c "import flask; print('FLASK_IMPORT_OK')"
```

这里复用第一阶段的板端环境，不再创建第二个文字环境。离线安装方法见 QUICKSTART。

下面只用于目标文件不存在的新安装。目标已存在时脚本主动停止，先确认它是否是在用版本：

```sh
python3 - <<'PY'
from pathlib import Path
source = Path('/userdata/rk3588-offline-assistant/components/web/web_service.py')
target = Path('/userdata/rkllama-web.py')
assert not target.exists() and not target.is_symlink(), '目标已存在，先检查并备份，未覆盖'
with target.open('xb') as output:
    output.write(source.read_bytes())
target.chmod(0o644)
print('WEB_FILE_READY')
PY
```

## 3. 启动网页服务

```sh
cd /userdata/rk3588-offline-assistant
export LOCAL_AI_PYTHON=/userdata/rk3588-offline-assistant/.venv/bin/python
python3 tools/web_control.py start
python3 tools/web_control.py status
```

预期 `LOCAL_WEB_STARTED ... port=8081`。新控制工具核对 PID、启动时间和准确参数，使用 pidfd 停止自己的进程；不会仅按 Python 程序名停止服务。

```sh
python3 - <<'PY'
import json
from urllib.request import urlopen
with urlopen('http://127.0.0.1:8081/api/status', timeout=5) as response:
    status = json.load(response)
assert status['online'] is True
assert 'qwen2-0.5b' in status['models']
print('WEB_PROXY_OK', status['models'])
PY
ip -4 addr
```

手机和板子连接同一个可互通的局域网，手机浏览器访问 `http://<板子IP>:8081/`。例如自己的板子地址为 192.168.x.x，就填实际地址；不要填 0.0.0.0 或 127.0.0.1。使用手机浏览器输入文字提问，收到回答才算本阶段完成。

模型服务 8080 仅监听本机；网页 8081 监听所有网卡供局域网访问。网页控制接口没有账号认证，只用于可信局域网，不做公网端口转发。

## 4. 此时哪些按钮能用

- 文字框、发送、当前页面聊天记录可用。
- 模型列表来自实际配置；首次只配置 Qwen 就只选 Qwen。
- “开始语音对话”控制开发板的麦克风和扬声器；完成下一章后再用。它不会调用手机麦克风。
- 电脑/手机网页和原生 Qt 应用是两个客户端；原生应用不是内嵌网页。

## 5. 停止、重新启动与故障检查

```sh
cd /userdata/rk3588-offline-assistant
python3 tools/web_control.py stop
python3 tools/service_control.py stop
```

停止网页会先请求停止它启动的语音子进程，再停止网页本身。重新启动仍按“模型 → 网页”顺序；没有设置启动项时，重启开发板后也要这样启动。

| 现象 | 检查 |
| --- | --- |
| 网页服务启动失败 | `tail -n 60 /userdata/rk3588-offline-assistant/web-service.log` |
| 模型服务失败 | `tail -n 60 /userdata/rk3588-offline-assistant/model-service.log` |
| localhost 正常，手机打不开 | 检查板卡当前 IP、手机网络、路由器访客隔离/设备互通；不是模型问题 |
| 服务端口被占用 | `ss -ltnp`（固件有此命令时）；确认是否已有部署，不要重复启动 |
| 控制工具说没有自己的服务，端口却在 | 可能是旧服务或手动运行的进程；回到其前台终端 Ctrl+C，不能用新工具接管未知 PID |
| 网页更新后仍显示旧内容 | 先确认文件版本和实际进程，再刷新/清除页面缓存 |

本章复制的是新文件，没有替换已有服务。撤回时先用控制工具停止，保留文件以供检查即可；不要删除模型或虚拟环境。下一章：[语音对话](VOICE_SETUP.md)。
