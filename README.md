# Medical Image Check

面向基础医学实验研究团队的 Windows 本地图像与 Excel 原始数据查重工具。

> 当前状态：需求 0.1 已确认，基础 Alpha 闭环正在持续开发。

GitHub 仓库：https://github.com/FelixLiu1996/medical-image-check

## 产品目标

- 查找图片完全重复、变换后重复、局部重叠、单图内部复制和高度相似内容。
- 优先支持 Western blot、Dot blot、荧光图和普通病理图片的专项分析。
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
- [Dot blot 本地评测](docs/DOT_BLOT_EVALUATION.md)
- [发布规范](docs/RELEASE.md)
- [跨窗口交接](MEMORY.md)

## 开发状态

当前开发分支已经具备：

- Python 3.12/PySide6 Essentials 中文桌面骨架；
- 现代化中文浅色首页，以及相互独立的图片查重、数据查重入口和文件类型过滤；
- 项目新建、打开、保存、旧格式迁移和最近扫描结果恢复；
- 后台扫描安全暂停、继续和取消；取消不会用未完成结果覆盖上一次完整扫描；
- 图片文件 SHA-256、跨格式解码像素指纹和整体感知近似候选；
- 多页 TIFF 逐页处理，以及旋转、翻转、压缩和缩放的全局候选；
- ORB 局部候选索引与 RANSAC 几何验证，可检测裁剪、大图包含小图和部分重叠；
- 双图并排证据预览、两侧匹配矩形及匹配点/内点/覆盖率/几何变换说明；
- Western blot 明暗极性归一化、横向条带/面板候选、分段索引，以及条带结构、排列几何和背景纹理联合验证；
- Western blot 同图面板 Copy-Move 与跨图曝光/翻转候选，单条带敏感检测可独立启用；
- Dot blot/斑点阵列专项检测，联合排列与逐斑点局部图像识别弱斑点、局部子集、裁剪、缩放、旋转、镜像和对比度变化；
- 荧光图 DAPI/FITC/RFP/Cy5 等通道识别、同视野配准、单通道与 Merge 成分关系，以及同通道高一致复用候选；
- 图片内容类型支持自动识别或手动指定通用、Western、Dot blot、荧光和病理，减少专项串类误报；
- 普通病理图光密度组织掩膜、多尺度局部区域匹配、倍率识别、染色准入，以及不同倍率正常关系分类；
- xlsx、xlsm、xls、csv 数值读取；
- Excel 完整数值和数值行重复检测；
- Excel 连续数字片段、可调近似容差、单次/连续四则运算、固定倍数/偏移、连续片段、乱序、少量修改、稳健线性、二维区域和低风险统计候选；
- 项目内可调 3–12 位数字片段阈值、Excel 容差/目标/风险阈值，以及 GUI/三种报告结构化数值证据；
- Excel 低信息量序列/区域和零乘积降噪，识别只读公式与 `rescaled` 等派生列正常关系；默认显示最多 50 条跨规则归并重点候选，并可切换查看全部线索；
- 隐藏工作表扫描、公式缓存缺失提示和损坏文件隔离；
- Excel、单文件 HTML 和 A4 PDF 三种本地报告；HTML 支持搜索/风险筛选和内嵌图像证据，PDF 支持中文归档、打印和图像证据；
- GUI 证据预览支持聚焦两侧图片匹配区域、Excel 两侧原表上下文/命中高亮和一键复制证据摘要；
- 候选结果支持可选的“准确、误报、正常关联”一键标记、状态筛选和清除标记；反馈随项目本地保存，同一算法版本的稳定结果重新扫描时继承；
- 支持导出只包含已标记结果的 Excel/JSON 算法反馈清单，记录算法版本、规则、位置和结构化证据，不复制原始图片或表格；
- 扫描记录图片解码、通用/专项特征、候选验证和 Excel 规则等分阶段耗时，探测 NVIDIA GPU/OpenCV CUDA 状态，并可导出不含原始路径和证据的 JSON 性能诊断；
- Windows/Linux GitHub CI、静态检查和自动化测试。
- `pyside6-deploy`/Nuitka Windows standalone 免安装包工作流。

当前整体感知、局部几何、Western blot、Dot blot、荧光、病理、Excel 近似和关系结果均需人工复核。Western blot 复杂多面板拆分、任意区域擦除/拼接，Dot blot 多行阵列/不规则排布，荧光实验组语义，病理连续切片语义，通用单图 Copy-Move，Excel 自动识别/手动框选扫描、历史库和可恢复任务断点仍在后续里程碑；统计相似不会自动判定重复。

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

当前界面可供团队进行 Alpha 体验，但检测结果仍必须人工复核，不应作为正式科研结论直接使用。

Windows 免安装包通过 GitHub Actions 的 `Windows portable package` 工作流构建；当前开发分支推送会触发验证，也保留手动和版本标签入口。当前产物是 Alpha 测试工件，不是正式 Release。

## 许可证

项目计划采用 Apache License 2.0。第三方代码、模型权重和资源保留各自许可证，发行前必须完成许可证审查并记录在 `NOTICE` 和 `THIRD_PARTY_NOTICES.md`。
