# 开发板首轮检查

这轮只准备候选目录、核对依赖，不更改旧服务，也不初始化 NPU 模型。
需要完整的 local-ai-candidate-v010.tar.gz；不能只传一个 Python 文件。

### Windows PowerShell 上传

```powershell
$candidatePackage = "C:\path\to\local-ai-candidate-v010.tar.gz"
$boardIp = "填写开发板当前IP"
scp "$candidatePackage" "root@${boardIp}:/userdata/"
```

填写本机压缩包路径和开发板当前 IP。如果 DHCP 地址变化，请更新地址。

### 开发板终端准备

```sh
cd /userdata
test ! -e local-ai-candidate-v010 && tar -xzf local-ai-candidate-v010.tar.gz
cd /userdata/local-ai-candidate-v010
python3 tools/verify_package.py
python3 tools/prepare_board.py --runtime /userdata/rkllama-lab/rkllama-83891fba4783dfe1dc93ce0168b8e826983c126e/src/rkllama/lib/librkllmrt.so
/userdata/rkllama-venv/bin/python tools/doctor.py
```

准备成功标志：CANDIDATE_CONFIG_OK。
检查成功标志：BOARD_PREFLIGHT_OK。
把检查工具的完整输出保留，用来确认两个分词器、动态库和驱动状态。
other_runtime_pids 出现旧 RKLLama 的 PID 在这轮是正常的；
实际推理前需要单独安排释放旧模型资源。

配置默认读取 /userdata/rkllama-models 下已有的两个 Modelfile，
并优先选各自 tokenizer 目录；没有它时再寻找配置中的本地分词器路径。
生成的 config.local.json 和 vendor-local 不进入开源源码。
如果已经有 config.local.json，准备脚本会拒绝覆盖，避免重跑改变配置。

### 可选：只查询模型列表

上面的检查通过后，在一个开发板终端前台运行：

```sh
sh run_candidate.sh --port 18080
```

再开一个开发板终端运行：

```sh
python3 - <<'PY'
import json
from urllib.request import urlopen
for path in ("/healthz", "/api/tags"):
    with urlopen("http://127.0.0.1:18080" + path, timeout=5) as response:
        print(path, json.loads(response.read()))
PY
```

此时 inference_enabled=false，询问模型会被拒绝，这是本轮的预期行为。
按 Ctrl+C 关闭候选服务。它不随开机启动，不占用旧 8080/8081/8082。

### 暂不执行的步骤

本轮不执行 --enable-inference，不停止 S99rkllama，不改网页代理地址，不删旧项目。
先用本轮输出核对实机环境，再安排带回退的真实推理试验。
启动推理后也只能说“本次请求成功”，不能把健康接口能访问当作 NPU 已通过验证。
