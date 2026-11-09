# 可选：Buildroot 缺少 libgomp.so.1

只在 ldd/doctor 报缺库时使用。优先使用板卡 SDK 中与板端兼容的库；本页另给一个可核对的 Debian arm64 文件来源，不在 Buildroot 安装 Debian 系统或替换 libc。

## 来源与限制

[Debian 官方包信息](https://packages.debian.org/bookworm/arm64/libgomp1/download)：libgomp1_12.2.0-14+deb12u1_arm64.deb，104092 字节，包 SHA-256 为 `a48b70dd1a95585466b40bd94564bce56ebcb2566e49ccdb6441e996aaab2098`。

本次实际下载、核对并只读分析了其中的库：AArch64，依赖 libc.so.6，最高要求 GLIBC_2.34；库 SHA-256 为 `9d8c6a6175f6a7cda286c80f4de577edd58077ac3f4102356ec546869d170d30`。这是与参考板 glibc 2.37 匹配的候选文件，**本次没有在板端重新加载验证它**；仍须 ldd、doctor、真实推理通过。glibc 低于 2.34、musl 或其他架构不要照用。

## 下载、提取、复制

电脑浏览器下载 [固定 Debian 包](https://deb.debian.org/debian/pool/main/g/gcc-12/libgomp1_12.2.0-14+deb12u1_arm64.deb)，核对包 SHA-256 后传到开发板 /userdata/local-ai-files。也可使用 Windows PowerShell：

```powershell
$package = Join-Path $env:USERPROFILE 'Downloads\libgomp1_12.2.0-14+deb12u1_arm64.deb'
Invoke-WebRequest -Uri 'https://deb.debian.org/debian/pool/main/g/gcc-12/libgomp1_12.2.0-14+deb12u1_arm64.deb' -OutFile $package -ErrorAction Stop
if ((Get-FileHash $package -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'a48b70dd1a95585466b40bd94564bce56ebcb2566e49ccdb6441e996aaab2098') {
  throw '软件包校验失败'
}
scp $package root@<板子IP>:/userdata/local-ai-files/
```

开发板执行。它只从已校验的软件包读取一个普通文件，写入不存在的目标，不把整个软件包解压到系统目录：

```sh
python3 - <<'PY'
import hashlib
import io
import tarfile
from pathlib import Path
root = Path('/userdata/local-ai-files')
data = (root / 'libgomp1_12.2.0-14+deb12u1_arm64.deb').read_bytes()
assert hashlib.sha256(data).hexdigest() == 'a48b70dd1a95585466b40bd94564bce56ebcb2566e49ccdb6441e996aaab2098'
assert data[:8] == b'!<arch>\n'
offset, payload = 8, None
while offset + 60 <= len(data):
    header = data[offset:offset + 60]
    size = int(header[48:58])
    name = header[:16].decode('ascii').strip().rstrip('/')
    offset += 60
    body = data[offset:offset + size]
    offset += size + size % 2
    if name.startswith('data.tar'):
        with tarfile.open(fileobj=io.BytesIO(body), mode='r:*') as archive:
            members = [m for m in archive.getmembers()
                       if m.isfile() and m.name.endswith('/libgomp.so.1.0.0')]
            assert len(members) == 1
            payload = archive.extractfile(members[0]).read()
assert payload is not None
assert hashlib.sha256(payload).hexdigest() == '9d8c6a6175f6a7cda286c80f4de577edd58077ac3f4102356ec546869d170d30'
target = root / 'libgomp.so.1'
with target.open('xb') as output:
    output.write(payload)
target.chmod(0o644)
print('LIBGOMP_EXTRACT_OK', target)
PY
```

Python 缺少 lzma 时，可在 Ubuntu 电脑使用 `dpkg-deb -x 软件包.deb 新建的临时目录`，从其 usr/lib/aarch64-linux-gnu 中复制真实 libgomp.so.1.0.0 文件，上传时命名为 libgomp.so.1；这条解包命令不执行安装。不要上传一个丢失目标的软链接，也不要上传 x86 库。

把它与 librkllmrt.so 放在同一目录，回到 QUICKSTART 运行 configure.py；工具会复制到 vendor-local。然后：

```sh
cd /userdata/rk3588-offline-assistant
LD_LIBRARY_PATH="$PWD/vendor-local${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ldd vendor-local/librkllmrt.so
.venv/bin/python tools/doctor.py
```

已有配置和库时不重复执行新安装覆盖步骤。先停止服务、备份并核对文件；copy_vendor 会拒绝静默替换不同的库。GNU 运行库保持自己的许可证，本项目不重新分发这个二进制。
