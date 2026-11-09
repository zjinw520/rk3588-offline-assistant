# 板端真实推理试运行

前提：已运行准备与检查工具，并得到 BOARD_PREFLIGHT_OK。
本轮继续使用已准备的 /userdata/local-ai-candidate-v010 和两个现有模型。
首块板端预检确认：kernel 5.10.209，glibc 2.37，RKNPU 0.9.8，
RKLLM Runtime 1.3.0，Transformers 4.57.6；两个本地分词器均可加载。
这仍不等于新后端已经通过真实模型推理。

### 操作

先在开发板屏幕退出助手，确认语音对话停止。测试期间不打开助手、发起网页问答或说唤醒词。
把 tools/trial_board.py 上传到候选目录的 tools 子目录。无需重新准备模型或覆盖现有配置。

在开发板运行：

```sh
cd /userdata/local-ai-candidate-v010
python3 tools/trial_board.py
```

脚本依次完成：

1. 检查旧 8080 服务、8081 语音状态、新端口空闲和运行库指纹。
2. 通过旧 PID 文件核对命令行和进程出生时间，记录恢复用的命令、工作目录和环境（环境不写入日志）。
3. 使用 Linux pidfd 终止确切的旧后端及已识别的子进程，避免 PID 被复用时误发信号。
4. 确认没有其他 RKLLM 进程后，用 18080 启动新后端。
5. 按 Qwen → DeepSeek → Qwen 的顺序执行三次流式问答，验证模型切换、完成标记和服务执行状态。
6. 卸载模型、关闭候选服务，恢复旧 8080 后端并检查原来的模型列表。

首次加载及 DeepSeek 生成可能需要较长时间。终端会打印开始测试的模型以及测试结果。
只修改旧后端的运行状态及对应 PID 文件，不改开机启动、8081 网页服务、桌面图标、模型或分词器。
因此测试结束后仍使用旧助手，新后端还未永久替换。

### 结果

- TRIAL_CHAT_OK：一次请求完成，记录模型名称、回答文字和时间。
- TRIAL_UNLOAD_OK：候选模型已经释放。
- OLD_BACKEND_RESTORED：原来的 8080 服务已恢复。
- NPU_TRIAL_OK_OLD_RESTORED：三次请求、卸载和恢复全部通过。

真实输出和环境记录在 trial-results.json；候选服务详细日志在 trial-server.log。
时间中的 first_chunk_seconds 是首个响应片段，不是正式回答首字；
DeepSeek 的首片段可能只是补回的思考标签，不能当作响应速度的基准。
done_reason=length 表示达到输出 token 上限，仍应检查回答是否完整。
通过本轮证明推理及切换链路可运行，不代表模型回答质量或全部语音功能已经验证。

### 失败处理

正常异常、模型加载失败和 Ctrl+C 都会进入恢复流程。
检查失败时不停止旧服务；发现无关运行库进程或 PID 文件被外部更改时，不盲目杀进程或重复启动。
Python/内核故障、强制杀死试运行脚本或断电无法保证执行恢复逻辑；
开机配置未改，正常重启会恢复原有部署。
如果看到 OLD_BACKEND_RESTORE_FAILED，先保留完整终端输出、trial-results.json 和日志，
不要继续手工启动第二个模型服务。

pidfd 接口来自 Python 3.10 标准库：
[os.pidfd_open](https://docs.python.org/3.10/library/os.html#os.pidfd_open)、
[signal.pidfd_send_signal](https://docs.python.org/3.10/library/signal.html#signal.pidfd_send_signal)。
若板上 Python 没有这些接口，脚本会在停止旧服务前退出。
