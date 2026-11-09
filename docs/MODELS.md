# 模型、分词器和运行库

仓库和入门包不附带外部模型、分词器、RKLLM Runtime、libgomp、Qt 或 sherpa-onnx。先在联网电脑下载，再传入板卡。失败时先检查 DNS、时间和证书信任，不要把空文件当模型。

新手可直接按 [电脑下载与上传步骤](MODEL_DOWNLOADS.md) 准备一个模型、匹配分词器和固定运行库。下面解释文件作用与兼容要求。

## 固定运行库

本版按官方 **Linux AArch64 RKLLM Runtime 1.3.0** 的固定文件开发，依据精确指纹核对 ABI，不是任意最新版均可使用。

- 官方仓库：[airockchip/rknn-llm](https://github.com/airockchip/rknn-llm)。
- [固定运行库下载](https://raw.githubusercontent.com/airockchip/rknn-llm/878f9361fd3afa7e167b7079918918f78d2c1c2a/rkllm-runtime/Linux/librkllm_api/aarch64/librkllmrt.so)。
- 提交：`878f9361fd3afa7e167b7079918918f78d2c1c2a`。
- 大小：`7617472` 字节。
- SHA-256：`6a9e4fc5324c68921c3a900340361e107af7599fe34dc8fa7759b2c5ae22a6e6`。

Linux 用 sha256sum，Windows 用 Get-FileHash 核对。版本号相同而指纹不同也会被拒绝，需要另行审核支持。

libgomp.so.1 是 GNU OpenMP 运行库。优先用板卡系统或与板端 glibc 匹配的工具链产物。参考板 glibc 为 2.37；SDK 产物曾要求 GLIBC_2.38，不能加载。不要直接覆盖板上 libc；用 ldd 和 doctor 确认依赖。

没有兼容库时，可参考 [libgomp 的官方候选文件与提取步骤](LIBGOMP.md)。该页明确记录静态检查范围，仍需板端验证。

## 已实测的语言模型

| 配置名 | 文件 | 实测大小 |
| --- | --- | --- |
| qwen2-0.5b | Qwen2-0.5B-Instruct_RK3588_w8a8.rkllm | 800441332 字节 |
| deepseek-r1-1.5b | DeepSeek-R1-Distill-Qwen-1.5B_RK3588_w8a8.rkllm | 2050726068 字节 |

它们是已转换到 RK3588 的社区产物。原始 safetensors、GGUF 或其他芯片的模型不能改后缀使用。文件名、大小、W8A8 或 .rkllm 后缀不能单独证明兼容；仍需匹配转换工具与 Runtime，并在板端推理。

Qwen 既有来源为 [HanzoHuang/Qwen2-0.5B-Instruct-RKLLM](https://huggingface.co/HanzoHuang/Qwen2-0.5B-Instruct-RKLLM) 的 RK3588 目录。本仓库不保证社区文件未来可用性，不自动下载或重分发。

DeepSeek 转换产物来源为 [HanzoHuang/DeepSeek-R1-Distill-Qwen-1.5B-RKLLM](https://huggingface.co/HanzoHuang/DeepSeek-R1-Distill-Qwen-1.5B-RKLLM)。已补充固定修订与 SHA-256，见下载教程。自行转换可参考 [官方 RKLLM 示例](https://github.com/airockchip/rknn-llm/tree/878f9361fd3afa7e167b7079918918f78d2c1c2a/examples)，并记录工具版本。原始模型页面和转换产物页面用途不同。

## 分词器

分词器把文本和聊天模板转成 token ID，不是提词器，也不负责生成回答。必须与转换模型对应，并完整保存在板上：

| 模型 | 原始分词器来源 |
| --- | --- |
| Qwen2-0.5B-Instruct | [Qwen 官方文件](https://huggingface.co/Qwen/Qwen2-0.5B-Instruct/tree/main) |
| DeepSeek-R1-Distill-Qwen-1.5B | [DeepSeek 官方文件](https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B/tree/main) |

准备对应仓库的 tokenizer_config.json、tokenizer.json、vocab.json、merges.txt、config.json、generation_config.json 等分词/配置文件，不需要再次下载 PyTorch 权重。本项目调用 AutoTokenizer 时使用 local_files_only=True、trust_remote_code=False，doctor 会尝试聊天模板。

## 可选语音模型

文字服务不需要这一组。语音源码所用文件：

| 文件或目录 | 作用 |
| --- | --- |
| sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/ | SenseVoice 识别，包含 model.int8.onnx、tokens.txt |
| sherpa-onnx-vits-zh-ll/ | VITS 中文合成和多音色，需模型、字典、词表及数据目录完整 |
| silero_vad.onnx | 判断说话与停顿，不进行语言问答 |

来源见 [sherpa-onnx 模型文档](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/index.html) 与 [官方发布页](https://github.com/k2-fsa/sherpa-onnx/releases)。分别核对模型许可，路径见 APPLICATION_LAYOUT.md。

BusyBox tar 可能不支持 -j。传入 tar.bz2 后可用 Python tarfile/bz2 处理，先确认模块及下载完整；解压失败不等于模型不存在。
