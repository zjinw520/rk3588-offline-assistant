# 贡献与问题反馈

欢迎板卡适配、可复现错误、测试和文档改进。请先阅读 README 的验证范围与 THIRD_PARTY.md。

```sh
python -m unittest discover -s tests -v
```

测试用 Python 3.10+ 标准库，不下载模型，不运行 NPU。没有编译产物时会跳过 native mock/ELF 检查，跳过不表示板端成功。修改 C++ 后需重新构建并做实机验证。

components 是在用源码固定快照，SOURCE_MANIFEST.json 校验原始字节。更改后说明行为差异，用 tools/update_component_manifest.py 更新清单并回归板端功能。

提交问题请提供板型/RAM、系统/内核、glibc/Python/RKNPU、Runtime 指纹、模型和分词器来源、复现步骤、预期结果、实际错误。区分模拟、生成音频和真实硬件验收。

不提交密码、令牌、私钥、私人录音和完整聊天日志。第三方模型、运行库、虚拟环境、SDK、个人备份不入库。新增第三方代码保留来源和许可。

优先事项：空白系统部署、完整语音/Qt 回归、可配置路径。效果或性能改进请提供同环境、同输入和同输出上限的对照。
