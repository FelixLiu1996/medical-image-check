# Medical Image Check

面向基础医学实验研究团队的 Windows 本地图像与 Excel 原始数据查重工具。

> 当前状态：需求 0.1 已确认，正在开发首个基础版本。

GitHub 仓库：https://github.com/FelixLiu1996/medical-image-check

## 产品目标

- 查找图片完全重复、变换后重复、局部重叠、单图内部复制和高度相似内容。
- 优先支持 Western blot、荧光图和普通病理图片的专项分析。
- 查找单个或多个 Excel 中的数值重复、连续片段、近似值、固定倍数和特定运算关系。
- 为每条结果提供可定位、可解释、可人工复核的证据。
- 完全本地运行，CPU 可用，兼容 GPU 加速，不调用大模型 API。

## 第一版形态

- Windows 10/11 x64 中文桌面软件
- GitHub 公有仓库和 GitHub Release
- 安装版、免安装版及可选模型包
- Apache-2.0 许可证

## 文档入口

- [产品需求](docs/PRD.md)
- [当前状态](docs/STATUS.md)
- [路线图](docs/ROADMAP.md)
- [架构初稿](docs/ARCHITECTURE.md)
- [算法方案](docs/ALGORITHMS.md)
- [数据模型](docs/DATA_MODEL.md)
- [界面流程](docs/UI_UX.md)
- [测试与验收](docs/TESTING.md)
- [发布规范](docs/RELEASE.md)
- [跨窗口交接](MEMORY.md)

## 开发状态

当前开发分支已经具备：

- Python 3.12/PySide6 Essentials 中文桌面骨架；
- 图片文件 SHA-256 完全重复检测；
- xlsx、xlsm、xls、csv 数值读取；
- Excel 完整数值和数值行重复检测；
- 隐藏工作表扫描、公式缓存缺失提示和损坏文件隔离；
- 版本化项目清单的保存与读取；
- Windows/Linux GitHub CI、静态检查和自动化测试。

局部图像重叠、数字片段、近似值、专项医学图像算法、历史库和完整报告仍在后续里程碑。

## 本地开发

需要 Python 3.12：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -c requirements/constraints.txt -e ".[dev]"
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
.venv/bin/python -m medical_image_check
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -c requirements/constraints.txt -e ".[dev]"
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m medical_image_check
```

当前界面明确标记为基础开发版，不应作为正式科研结论工具使用。

## 许可证

项目计划采用 Apache License 2.0。第三方代码、模型权重和资源保留各自许可证，发行前必须完成许可证审查并记录在 `NOTICE` 和 `THIRD_PARTY_NOTICES.md`。
