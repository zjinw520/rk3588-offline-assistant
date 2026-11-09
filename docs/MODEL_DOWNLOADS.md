# 在电脑准备语言模型、分词器和运行库

本页服务于 QUICKSTART 的第三步。Windows PowerShell 下载，随后传到开发板。先选 Qwen，后续需要第二个模型时再选 DeepSeek；不用先下载两个。下载期间需要互联网，板端运行使用本地文件。

## 1. 下载一个模型及对应分词器

在 Windows PowerShell 执行下面的完整块。第一次保持 `$selection = 'qwen'`；下载 DeepSeek 时改成 deepseek，重新执行该块。目录在电脑 Downloads 下，已有同名下载文件会重新下载；不要指向在用板端模型。

```powershell
$ErrorActionPreference = 'Stop'
$selection = 'qwen'
$filesDir = Join-Path $env:USERPROFILE 'Downloads\local-ai-files'
New-Item -ItemType Directory -Force $filesDir | Out-Null
if ($selection -eq 'qwen') {
  $repo = 'HanzoHuang/Qwen2-0.5B-Instruct-RKLLM'
  $revision = '624282d3e8d4612eecbb54762a8f492a3ff91d29'
  $name = 'Qwen2-0.5B-Instruct_RK3588_w8a8.rkllm'
  $expected = '24e99eeea1512d6e7f3f120f3344a510933301dfcb72bd64b24a9724ab48e1d8'
  $tokenRepo = 'Qwen/Qwen2-0.5B-Instruct'
  $tokenRevision = 'c540970f9e29518b1d8f06ab8b24cba66ad77b6d'
  $tokenDir = Join-Path $filesDir 'qwen2-tokenizer'
  $tokenFiles = @('config.json','generation_config.json','tokenizer.json','tokenizer_config.json','merges.txt','vocab.json')
} elseif ($selection -eq 'deepseek') {
  $repo = 'HanzoHuang/DeepSeek-R1-Distill-Qwen-1.5B-RKLLM'
  $revision = '922b334537ce974ed7bc92c792143d928da3aa46'
  $name = 'DeepSeek-R1-Distill-Qwen-1.5B_RK3588_w8a8.rkllm'
  $expected = 'd00038d619c1905ac66ab5d499019d80889d37cc1f81be491cc503e284950c2d'
  $tokenRepo = 'deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B'
  $tokenRevision = 'ad9f0ae0864d7fbcd1cd905e3c6c5b069cc8b562'
  $tokenDir = Join-Path $filesDir 'deepseek-tokenizer'
  $tokenFiles = @('config.json','generation_config.json','tokenizer.json','tokenizer_config.json')
} else { throw 'selection 只支持 qwen 或 deepseek' }
$modelFile = Join-Path $filesDir $name
$url = 'https://huggingface.co/{0}/resolve/{1}/RK3588/{2}?download=true' -f $repo,$revision,$name
Invoke-WebRequest -Uri $url -OutFile $modelFile
$actual = (Get-FileHash $modelFile -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "模型校验失败：$name" }
New-Item -ItemType Directory -Force $tokenDir | Out-Null
foreach ($file in $tokenFiles) {
  $url = 'https://huggingface.co/{0}/resolve/{1}/{2}?download=true' -f $tokenRepo,$tokenRevision,$file
  $target = Join-Path $tokenDir $file
  Invoke-WebRequest -Uri $url -OutFile $target
  if ((Get-Item $target).Length -eq 0) { throw "分词器文件为空：$file" }
}
Write-Output "MODEL_DOWNLOAD_OK $selection"
Get-Item $modelFile | Select-Object Name,Length
```

两个分词器文件结构不同，DeepSeek 在这个修订中不需要 Qwen 的 merges.txt/vocab.json。不要把一个目录里的配置混到另一个模型里。板端 doctor 还会检查能否离线加载聊天模板。

## 2. 下载本版固定运行库

在同一个 PowerShell 窗口执行：

```powershell
$runtimeFile = Join-Path $filesDir 'librkllmrt.so'
$runtimeUrl = 'https://raw.githubusercontent.com/airockchip/rknn-llm/878f9361fd3afa7e167b7079918918f78d2c1c2a/rkllm-runtime/Linux/librkllm_api/aarch64/librkllmrt.so'
Invoke-WebRequest -Uri $runtimeUrl -OutFile $runtimeFile
$actual = (Get-FileHash $runtimeFile -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne '6a9e4fc5324c68921c3a900340361e107af7599fe34dc8fa7759b2c5ae22a6e6') {
  throw '运行库校验失败'
}
Write-Output 'RUNTIME_DOWNLOAD_OK'
```

系统缺少 libgomp.so.1 时另准备匹配板端架构和 glibc 的库，见 MODELS.md；本页不从随机网站下载系统库。

## 3. 传入开发板

先把 `$boardIp` 改成自己的实际地址。新安装时目标目录可以不存在；已有在用同名模型时不要用本步骤覆盖它，先停止服务并备份检查。

```powershell
$boardIp = '填写开发板当前IP'
ssh "root@$boardIp" 'mkdir -p /userdata/local-ai-files'
if ($LASTEXITCODE -ne 0) { throw '无法准备板端目录' }
foreach ($file in @($modelFile,$runtimeFile)) {
  scp $file "root@${boardIp}:/userdata/local-ai-files/"
  if ($LASTEXITCODE -ne 0) { throw '文件传输失败，先检查后重传' }
}
scp -r $tokenDir "root@${boardIp}:/userdata/local-ai-files/"
if ($LASTEXITCODE -ne 0) { throw '分词器传输失败' }
```

开发板核对所传模型的 SHA-256，然后回到 QUICKSTART 配置该模型：

```sh
sha256sum /userdata/local-ai-files/*.rkllm /userdata/local-ai-files/librkllmrt.so
```

分词器路径分别是 /userdata/local-ai-files/qwen2-tokenizer、/userdata/local-ai-files/deepseek-tokenizer。脚本只准备文件，不初始化模型、不改驱动。

## 来源与已核对范围

2026-11-09 查询了模型发布者公开元数据，固定上述模型仓库修订；文件名、大小、LFS SHA-256 与参考模型记录/保留的上传源文件校验对应。分词器来自原模型官方仓库，固定本次核对的修订和文件清单；这些新锁定的分词器修订仍需在目标板按 doctor 和真实问答验证。

- [Qwen 转换产物](https://huggingface.co/HanzoHuang/Qwen2-0.5B-Instruct-RKLLM/tree/624282d3e8d4612eecbb54762a8f492a3ff91d29)。
- [DeepSeek 转换产物](https://huggingface.co/HanzoHuang/DeepSeek-R1-Distill-Qwen-1.5B-RKLLM/tree/922b334537ce974ed7bc92c792143d928da3aa46)。
- [Qwen 官方分词器](https://huggingface.co/Qwen/Qwen2-0.5B-Instruct/tree/c540970f9e29518b1d8f06ab8b24cba66ad77b6d)。
- [DeepSeek 官方分词器](https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B/tree/ad9f0ae0864d7fbcd1cd905e3c6c5b069cc8b562)。

文件仍由各自发布者提供，不随本项目再分发。网络受限时可在浏览器打开这些固定修订下载同名文件；不要用关闭证书验证或改后缀的方式绕过失败。
