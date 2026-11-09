# 接入既有助手与回退

本工具用于当前 ATK-DLRK3588 项目的迁移，不是适用于任意系统的通用安装器。
先完成 prepare_board.py、doctor.py、trial_board.py；保留旧部署及两个 Python 环境。
现有网页、Qt 界面、麦克风/扬声器和桌面唤醒代码不在本轮替换范围内。

## 安装

确保语音对话已经停止，且候选目录有 service_control.py、install_backend.py 和 trial_board.py。

```sh
cd /userdata/local-ai-candidate-v010
python3 tools/install_backend.py
```

安装工具会：

1. 验证现有启动脚本布局与当前旧后端身份。
2. 在 backups/backend-before-v1-* 完整备份原 S99rkllama，记录 SHA-256 和权限。
3. 临时停止原模型后端。
4. 只替换 S99rkllama 的模型启动、停止、状态分支，保留原网页启动分支。
5. 启动独立后端并确认模型列表与健康状态。
6. 记录当前恢复点到 active-backend-backup.txt。

成功标志为 INDEPENDENT_BACKEND_INSTALLED port=8080。
8081 网页和 8082 原生窗口控制的接口地址不变。
网页中保留的 RKLLama 字样属于迁移期间的旧命名，并不表示模型请求仍由旧框架执行。
运行时以 /healthz 返回的 backend=local-ai-independent 及进程命令行判断。

## 服务与文件

- tools/service_control.py：仅管理本项目模型进程，使用 PID、出生时间、准确参数及 pidfd 校验。
- run/model-service.json：当前模型服务身份；不是模型文件，不保存登录密码。
- model-service.log：新后端启动与 SDK 日志。
- backups/backend-before-v1-*/：旧启动脚本、安装后脚本和校验信息，属于恢复点。
- /userdata/rkllama-lab/rkllama-server.pid：沿用旧位置，供既有启动脚本兼容；不能仅凭这里的 PID 随意发信号。
- vendor-local：运行时复制到本机的官方库；不进入开源发布包。

根目录及模型位置保持不变。服务当前复用 /userdata/rkllama-venv 中已安装的分词器依赖，
不导入 RKLLama 包；后续可在整理完整发行版时迁移到单独命名的环境。

## 回退

停止对话后执行安装输出中的确切恢复命令，例如：

```sh
python3 tools/install_backend.py --rollback /userdata/local-ai-candidate-v010/backups/backend-before-v1-填写实际编号
```

恢复点必须位于本应用 backups 目录。工具会核对原文件及当前启动文件的哈希，
若安装后有人手动修改过启动脚本，会拒绝直接覆盖。
回退会停止确切的新后端、恢复旧启动脚本并等待旧接口就绪；不会删除模型和分词器。

本板已经实测回退到旧 RKLLama、再重新安装新后端。
曾发现旧服务启动后就绪稍慢导致误报，已改为最长 30 秒等待，并补充回归测试。
