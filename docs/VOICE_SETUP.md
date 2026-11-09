# 第三阶段：开发板语音对话

前提：第二阶段模型 8080、网页 8081 已正常工作。参考声卡是原厂 ES8388（ALSA 名称 rockchipes8388），Python 3.10。先停止网页中的语音对话和其他录音程序。下文只给新安装复制文件，不覆盖你的既有助手。

## 1. 确认硬件与原厂音量

开发板终端执行：

```sh
cat /proc/asound/cards
arecord -l
aplay -l
command -v arecord
command -v aplay
command -v amixer
cat /opt/ui/src/apps/resource/volume
```

应看到可录音、可播放的 rockchipes8388，三个 ALSA 命令存在，原厂音量文件为 0～100 整数。使用声卡名称，避免每次启动编号改变。其他声卡须替换后面的录音/播放设备参数；本版桌面待唤醒程序还固定使用这个声卡名称。

在原厂屏幕把音量调到适当水平，先用较低音量测试。音量文件为零时，本程序的软件增益也为零。声卡存在只表示驱动暴露了设备，不能证明实际麦克风、喇叭已连接或录音路由正确。

## 2. 下载三个语音模型

在电脑浏览器分别下载以下官方发布文件；也可在 Windows PowerShell 执行：

```powershell
$voiceDir = Join-Path $env:USERPROFILE 'Downloads\rk3588-voice-models'
New-Item -ItemType Directory -Force $voiceDir | Out-Null
$assets = @(
  'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2',
  'https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/sherpa-onnx-vits-zh-ll.tar.bz2',
  'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx'
)
foreach ($url in $assets) {
  $name = ($url -split '/')[-1]
  Invoke-WebRequest -Uri $url -OutFile (Join-Path $voiceDir $name) -ErrorAction Stop
}
Get-ChildItem $voiceDir | Get-FileHash -Algorithm SHA256
ssh root@<板子IP> 'mkdir -p /userdata/voice-models'
foreach ($url in $assets) {
  $name = ($url -split '/')[-1]
  scp (Join-Path $voiceDir $name) root@<板子IP>:/userdata/voice-models/
  if ($LASTEXITCODE -ne 0) { throw "上传未完成：$name" }
}
```

下载出处：[SenseVoice 官方说明](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html)、[VITS 官方说明](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/vits.html)、[模型索引](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/index.html)。它们分别负责识别、合成和检测说话起止；本项目这三部分运行在 CPU。

证书、DNS 或网络失败时先修复连接，也可用浏览器正常下载；不关闭证书验证。不把“文件已存在”视为下载完整。SCP 断开后重新传输，并比较电脑和板子的 SHA-256：

```sh
sha256sum /userdata/voice-models/*.tar.bz2 /userdata/voice-models/silero_vad.onnx
```

## 3. 解压模型

BusyBox tar 可能没有 -j；下面用 Python 3.10 的 bz2/tarfile 解压，只接受预期目录中的普通文件和目录。已有同名模型目录会停止，保留原文件，不覆盖。

```sh
python3 - <<'PY'
import tarfile
from pathlib import Path, PurePosixPath
root = Path('/userdata/voice-models')
assert not root.is_symlink()
names = (
    'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17',
    'sherpa-onnx-vits-zh-ll',
)
for name in names:
    target = root / name
    assert not target.exists() and not target.is_symlink(), '模型目录已存在：' + str(target)
    with tarfile.open(root / (name + '.tar.bz2'), 'r:bz2') as archive:
        members = archive.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            assert not path.is_absolute() and '..' not in path.parts and '\\' not in member.name
            assert path.parts and path.parts[0] == name, '非预期目录'
            assert member.isfile() or member.isdir(), '拒绝链接或特殊文件'
        archive.extractall(root, members=members)
    print('EXTRACT_OK', name)
PY
```

如果第一次只解压完一包后失败，先检查失败包；重试时把已完成的名称从 names 中去掉。不要删除在用模型目录。

预期文件：

```text
/userdata/voice-models/
├── silero_vad.onnx
├── sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/
│   ├── model.int8.onnx
│   └── tokens.txt
└── sherpa-onnx-vits-zh-ll/
    ├── model.onnx
    ├── lexicon.txt
    ├── tokens.txt
    ├── phone.fst
    ├── date.fst
    └── number.fst
```

完整保留包内其他文件。若 Python 缺 bz2 模块，在电脑解压并通过 SCP 上传完整目录，或补对应固件功能。

## 4. 复制语音程序并建立环境

在开发板执行。这个块只用于没有 voice-assistant 和 voice-venv 的新安装：

```sh
python3 - <<'PY'
from pathlib import Path
import re
import shutil
root = Path('/userdata/rk3588-offline-assistant')
target = Path('/userdata/voice-assistant')
assert not target.exists() and not target.is_symlink(), '已有助手目录，先备份检查，未覆盖'
assert not Path('/userdata/voice-venv').exists(), '已有语音环境，先确认用途'
shutil.copytree(root / 'components/voice', target)
# 只修改部署副本的动态库路径，保留仓库内源码快照及其原始哈希。
runner = target / 'run_voice_assistant.sh'
text, count = re.subn(r'^RKLLAMA_LIB=.*$', 'RKLLAMA_LIB=' + str(root / 'vendor-local'),
                      runner.read_text(encoding='utf-8'), count=1, flags=re.M)
assert count == 1
runner.write_text(text, encoding='utf-8')
runner.chmod(0o755)
wake = target / 'desktop_wake.py'
text, count = re.subn(r'^LIB = ".*"$', 'LIB = "' + str(root / 'vendor-local') + '"',
                      wake.read_text(encoding='utf-8'), count=1, flags=re.M)
assert count == 1
wake.write_text(text, encoding='utf-8')
print('VOICE_FILES_READY')
PY
python3 -m venv /userdata/voice-venv
/userdata/voice-venv/bin/python -m pip install -r /userdata/rk3588-offline-assistant/components/requirements-board-voice.txt
export LD_LIBRARY_PATH="/userdata/rk3588-offline-assistant/vendor-local${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
/userdata/voice-venv/bin/python -c "import numpy, sherpa_onnx; print('VOICE_IMPORT_OK')"
```

## 5. 分别验证扬声器、麦克风和识别

后面的直接 Python 测试沿用上一节的动态库环境；新开 SSH 终端时先重新执行那条 LD_LIBRARY_PATH 导出。run_voice_assistant.sh 自身会设置对应路径。

先播放三个低幅测试音，不加载任何模型：

```sh
sh /userdata/voice-assistant/run_voice_assistant.sh --audio-test
```

听到测试音并出现 AUDIO_TEST_DONE 才继续。无声时查看原厂音量、连接与路由；用 `amixer -c rockchipes8388 cget name='PCM Volume'` 只读检查硬件增益，不要把原始 0～192 刻度当成屏幕百分比。

再录一小段并回放。执行后对板子麦克风说“你好，这是录音测试”：

```sh
cd /userdata/voice-assistant
/userdata/voice-venv/bin/python - <<'PY'
from pathlib import Path
import voice_assistant as voice
voice.record_audio('hw:CARD=rockchipes8388,DEV=0', 5, Path('question.wav'))
print('RECORD_WAV_OK')
PY
aplay -D plughw:CARD=rockchipes8388,DEV=0 /userdata/voice-assistant/question.wav
```

参考固件的 arecord 可能实际录完却返回 1，程序会按 WAV 格式、帧数和时长判断，不能单看退出码。听到自己的录音才算物理麦克风链路通过。此录音只存在本机，不上传仓库。

只测试识别，不连接语言模型：

```sh
cd /userdata/voice-assistant
/userdata/voice-venv/bin/python - <<'PY'
from pathlib import Path
import voice_assistant as voice
recognizer = voice.create_recognizer(voice.DEFAULT_ASR_DIR, 2)
text, seconds = voice.transcribe(recognizer, Path('question.wav'))
assert text.strip(), '未识别到文字，先检查录音'
print('ASR_OK', text, 'seconds=', round(seconds, 3))
PY
```

只测试中文合成与播放：

```sh
cd /userdata/voice-assistant
/userdata/voice-venv/bin/python - <<'PY'
from pathlib import Path
import voice_assistant as voice
tts = voice.create_tts(voice.DEFAULT_TTS_DIR, 4)
voice.synthesize_and_play(tts, '你好，我是本地语音助手。', 2, 1.25,
                          Path('answer.wav'), 'plughw:CARD=rockchipes8388,DEV=0')
print('TTS_PLAY_OK')
PY
```

## 6. 串起完整对话

确认 8080/8081 仍在运行，先用手动录音模式：

```sh
sh /userdata/voice-assistant/run_voice_assistant.sh --model qwen2-0.5b --speaker 2 --speed 1.25 --max-speech-chars 600
```

按回车、说问题，等待识别和播报，输入 q 回车退出。然后测试自动唤醒：

```sh
sh /userdata/voice-assistant/run_voice_assistant.sh --wake-test
```

说“你好小助手”，听到提示音后提问；应有 WAKE_TEST_OK。这个测试不加载语言模型或 TTS。完成后启动完整自动模式：

```sh
sh /userdata/voice-assistant/run_voice_assistant.sh --auto --model qwen2-0.5b --speaker 2 --speed 1.25 --max-speech-chars 600
```

说唤醒词 → 提示音 → 提问 → 等待播报。Ctrl+C 停止。此时“自动”表示这个程序正在监听；在原厂桌面上喊一声自动打开应用，需要第五阶段的桌面待唤醒服务。

语音指令示例：唤醒后说“调高音量”“音量调到百分之三十”“换个音色”“语速快一点”“切换到千问”“结束对话”。切换 DeepSeek 前必须已配置并验证该模型。未启动原生窗口时，“结束对话”只结束语音程序；第四阶段后才会同时通过窗口退出流程返回桌面。

## 7. 用网页开始和停止语音

先 Ctrl+C 停掉手动启动的语音进程，再在网页选择 Qwen、音色/语速，点“开始语音对话”。对开发板说话，页面不会读取手机麦克风。

停止按钮应终止语音，检查：

```sh
python3 - <<'PY'
import json
from urllib.request import urlopen
with urlopen('http://127.0.0.1:8081/api/voice/status', timeout=5) as response:
    print(json.load(response))
PY
tail -n 60 /userdata/voice-assistant/voice-assistant.log
```

running=false 表示网页管理的语音已停止。手动在其他终端启动的进程应回到那个终端停止，不由网页 PID 记录接管。

| 问题 | 先检查 |
| --- | --- |
| 连续录音停止/声卡忙 | 是否已有录音、前台语音或后台待唤醒；声卡名称是否对应 ES8388 |
| 有 WAV 但识别空白 | 先回放真实录音，检查输入路由、声道与音量 |
| 有文字但无声音 | 先做 TTS_PLAY_OK，确认音量文件、aplay 和实际喇叭 |
| 首句等待很久 | 首次模型加载、R1 思考和 TTS 都会耗时；先用 Qwen，查看分阶段日志 |
| 只说半句 | 检查模型 max_new_tokens、max-speech-chars 和生成是否正常结束；它们是不同的上限 |
| 开始/结束有啪声 | 连续 PCM 与柔化边缘可减少段间开关声；最前后还可能有声卡/功放开关声 |
| 模块导入失败 | 确认用 voice-venv 的 Python，检查对应 AArch64 wheel 与 ldd 依赖 |

恢复方法：先停止本章启动的语音进程，网页文字功能可以继续使用。保留模型及录音用于排查，不改原厂声卡驱动。下一章：[原生桌面应用](DESKTOP_SETUP.md)。
