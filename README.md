# Medical Image Check

面向基础医学实验研究团队的 Windows 本地图像与 Excel 原始数据查重工具。

> 当前状态：需求 0.1 已确认，基础 Alpha 闭环正在持续开发。

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
- 项目新建、打开、保存、旧格式迁移和最近扫描结果恢复；
- 图片文件 SHA-256、跨格式解码像素指纹和整体感知近似候选；
- 多页 TIFF 逐页处理，以及旋转、翻转、压缩和缩放的全局候选；
- ORB 局部候选索引与 RANSAC 几何验证，可检测裁剪、大图包含小图和部分重叠；
- 双图并排证据预览、两侧匹配矩形及匹配点/内点/覆盖率/几何变换说明；
- Western blot 明暗极性归一化、横向条带/面板候选、分段索引，以及条带结构、排列几何和背景纹理联合验证；
- Western blot 同图面板 Copy-Move 与跨图曝光/翻转候选，单条带敏感检测可独立启用；
- xlsx、xlsm、xls、csv 数值读取；
- Excel 完整数值和数值行重复检测；
- Excel 连续数字片段、近似值及连续列固定倍数、偏移、目标和/积检测；
- 项目内可调 3–12 位数字片段阈值，以及 GUI/报告结构化数值证据；
- 隐藏工作表扫描、公式缓存缺失提示和损坏文件隔离；
- 基础 Excel 报告，包含概览、结果、图像/数值证据、提示和项目输入；
- Windows/Linux GitHub CI、静态检查和自动化测试。
- `pyside6-deploy`/Nuitka Windows standalone 免安装包工作流。

当前整体感知、局部几何、Western blot、Excel 近似和关系结果均需人工复核。Western blot 复杂多面板拆分、任意区域擦除/拼接，通用单图 Copy-Move，Excel 自定义容差/单次运算/顺序打乱/统计异常，荧光/病理专项算法、历史库和 HTML/PDF 完整报告仍在后续里程碑。

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

Windows 免安装包通过 GitHub Actions 的 `Windows portable package` 工作流构建；当前开发分支推送会触发验证，也保留手动和版本标签入口。当前产物是 Alpha 测试工件，不是正式 Release。

## 许可证

项目计划采用 Apache License 2.0。第三方代码、模型权重和资源保留各自许可证，发行前必须完成许可证审查并记录在 `NOTICE` 和 `THIRD_PARTY_NOTICES.md`。
