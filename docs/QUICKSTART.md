# 独立模型服务入门

目标：在 RK3588 板上用 NPU 回答一个文字问题。**不需要安装 RKLLama；本页不安装完整语音/Qt 应用。** 参考环境是 ATK-DLRK3588 8GB，其他镜像先核对 Python、glibc、RKNPU 驱动和动态库依赖。

## 1. 下载并传入开发板

在电脑下载仓库 `packages/` 中的 `rk3588-offline-assistant-v0.1.0-aarch64.tar.gz` 和 `SHA256SUMS`，核对 SHA-256，然后通过 SCP 或 U 盘传到板子的 `/userdata/`。使用自己的板卡 IP：

```powershell
# Windows PowerShell
Get-FileHash .\rk3588-offline-assistant-v0.1.0-aarch64.tar.gz -Algorithm SHA256
scp .\rk3588-offline-assistant-v0.1.0-aarch64.tar.gz root@<板子IP>:/userdata/
```

下面所有命令在**开发板终端**执行。已有同名目录时换一个空目录，不覆盖在用服务：

```sh
cd /userdata
tar -xzf rk3588-offline-assistant-v0.1.0-aarch64.tar.gz
cd rk3588-offline-assistant
python3 tools/verify_package.py .
```

预期 `PACKAGE_OK`。包中已有 `build/liblocalai-rkllm130.so`，这是本项目的 C++ 适配库。GitHub 自动生成的源码 ZIP 不含该 build 文件；使用源码 ZIP 时需要先交叉编译，或从入门包取得对应适配库。

## 2. 板端 Python 环境

需要 Python 3.10+、venv、pip、ssl、ctypes。推荐独立环境：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

依赖固定为参考板实测版本，不是所有 Python/镜像的通用锁文件。不要把电脑 x86 虚拟环境复制到 AArch64 板上。缺少 wheel、系统库或 HTTPS 支持时先补板卡环境；Buildroot 通常没有 apt。已有验证成功的板端环境可通过 `LOCAL_AI_PYTHON=/绝对路径/bin/python` 指定。

离线安装需先准备**匹配板端 Python 和 AArch64** 的完整 wheel 集合，再用 `pip install --no-index --find-links /路径/wheels -r requirements.txt`。模型、分词器和运行库也须预先传入。

## 3. 外部文件与配置

按 [模型与外部文件](MODELS.md) 准备本版固定的官方 RKLLM 1.3.0 AArch64 运行库、RK3588 W8A8 Qwen `.rkllm` 和匹配的完整本地分词器。系统没有 libgomp.so.1 时还需要取得兼容库。例如：

```text
/userdata/local-ai-files/
├── librkllmrt.so
├── libgomp.so.1                 # 系统已有时可不放
├── Qwen2-0.5B-Instruct_RK3588_w8a8.rkllm
└── qwen2-tokenizer/
    ├── tokenizer_config.json
    ├── tokenizer.json
    ├── vocab.json
    ├── merges.txt
    ├── config.json
    └── generation_config.json
```

第一次先配置一个模型。按实际位置修改下面的路径：

```sh
python tools/configure.py \
  --runtime /userdata/local-ai-files/librkllmrt.so \
  --model qwen2-0.5b \
    /userdata/local-ai-files/Qwen2-0.5B-Instruct_RK3588_w8a8.rkllm \
    /userdata/local-ai-files/qwen2-tokenizer
python tools/doctor.py
```

配置工具生成 config.local.json，复制运行库到本目录 vendor-local，不改开机启动或已有模型，不覆盖已有配置。同目录存在 libgomp.so.1 时会复制，也可用 `--libgomp /实际路径/libgomp.so.1` 指定。

预期 `STANDALONE_CONFIG_OK`、`BOARD_PREFLIGHT_OK`。doctor **不初始化 NPU**；通过不表示模型已能推理。报告 other_runtime_pids 时，先通过该服务自己的停止方式释放 NPU，不要盲目 killall python。

## 4. 启动并提问

保持当前板端终端运行服务：

```sh
sh run_candidate.sh --enable-inference --port 18080
```

另开一个板端终端：

```sh
cd /userdata/rk3588-offline-assistant
python3 tools/chat.py --list
python3 tools/chat.py --model qwen2-0.5b --question '请用一句话介绍你自己。'
```

首个请求会加载模型；看到流式文字并正常结束才算推理通过。服务终端 Ctrl+C 停止。没有 --enable-inference 时只查询状态；默认不监听局域网。

从电脑调试，可建立 SSH 转发并保持该窗口打开：

```sh
ssh -L 18080:127.0.0.1:18080 root@<板子IP>
```

随后电脑访问 http://127.0.0.1:18080/healthz，或在电脑用 Python 运行同一个 tools/chat.py。这是 API 服务，根路径没有聊天网页。完整网页/语音组件见 [应用布局](APPLICATION_LAYOUT.md)。

## 5. 添加第二个模型

停止服务，在 config.local.json 的 models 数组内增加一条记录，保留 Qwen，无需删除。DeepSeek 示例：

```json
{
  "name": "deepseek-r1-1.5b",
  "path": "/userdata/local-ai-files/DeepSeek-R1-Distill-Qwen-1.5B_RK3588_w8a8.rkllm",
  "tokenizer": "/userdata/local-ai-files/deepseek-tokenizer",
  "context": 1024,
  "max_new_tokens": 512,
  "temperature": 0.7
}
```

这是**数组中的一个对象，不是完整配置文件**。完整结构见根目录 config.example.json。R1 使用 deepseek-r1 开头的名称，以启用对应思考前缀处理。重新运行 doctor 和服务，用 `--model deepseek-r1-1.5b` 提问。CLI 展示原始文字，可能包含 `<think>`；语音组件另有过滤。增大输出上限会增加等待时间。

本页不设置开机启动。旧部署迁移工具依赖特定历史目录；新用户不要直接执行 trial_board.py 或 install_backend.py。
