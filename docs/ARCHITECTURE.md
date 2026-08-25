# 架构初稿

状态：基础架构已实现，持续演进。

## 已选技术栈

- Python 3.12
- PySide6 Essentials / Qt Widgets
- openpyxl、xlrd 和标准库 csv
- NumPy、opencv-python-headless
- pytest、Ruff
- Windows/Linux GitHub Actions

详细取舍见 `decisions/ADR-0002-python-pyside-modular-monolith.md`。

## 架构目标

- Windows 本地单机运行。
- CPU 为完整基线，GPU 是可选加速后端。
- 数千张图片和十万个数值单元格场景下避免朴素全量精查。
- 扫描任务可暂停、恢复、取消并持续产生结果。
- 项目、算法、模型和数据格式可版本化。
- 原始文件只读。

## 逻辑模块

1. 桌面 UI：项目、导入、任务、结果、历史库、设置。
2. 应用服务：协调项目生命周期和用户操作。
3. 输入解析：静态图、TIFF 页、Excel 工作簿和 CSV。
4. 图像引擎：规范化、候选召回、几何验证、专项检测和证据生成。
5. Excel 引擎：数值提取、索引、序列、近似、变换和统计异常检测。
6. 任务调度：阶段、进度、资源限制、断点和取消。
7. 索引与历史库：指纹、特征、来源、人工结论和版本。
8. 结果与复核：重复组、证据、状态和备注。
9. 报告：Excel、HTML、PDF。
10. 模型运行时：模型发现、许可证清单、CPU/GPU 后端和回退。

## 基本数据流

输入只读解析 → 文件指纹与规范化 → 快速特征索引 → 候选召回 → 专项精查 → 规则评分 → 重复组 → 人工复核 → 报告/历史库。

## 隔离原则

- UI 不直接依赖具体算法实现。
- 图像和 Excel 引擎通过版本化任务与结果契约交互。
- GPU 后端不得成为业务逻辑分支；相同模型应具备 CPU 执行路径。
- 报告消费稳定结果模型，不直接读取算法内部临时数据。
- 项目包与内部数据库格式分离，项目包需要显式版本和迁移。

## 当前代码映射

| 逻辑层 | 路径 |
| --- | --- |
| 领域模型 | `src/medical_image_check/domain/` |
| 查重引擎 | `src/medical_image_check/engines/` |
| 格式与持久化 | `src/medical_image_check/infrastructure/` |
| 应用服务 | `src/medical_image_check/services/` |
| Qt UI | `src/medical_image_check/ui/` |
| 自动化测试 | `tests/` |

当前 `BasicScanService` 已贯通输入收集、图片文件/解码像素指纹、图片整体近似候选、局部描述子索引与几何验证、Western blot 条带/背景、荧光通道关系、病理组织多尺度专项验证、Excel 数值解析/片段/近似/序列关系、结果持久化和 UI 展示。通用与三类医学专项特征共享同一次页面解码；各检测器通过稳定 `Finding` 契约输出，不把内部数组写入项目文件。`ScanControl` 使用线程安全事件在文件边界、算法阶段和候选验证批次提供协作式暂停/继续/取消；取消抛出独立状态，不保存部分结果。

项目清单已接入 UI，能够恢复输入路径、片段设置、Western blot 单条带开关、最近一次结果、扫描提示和报告路径。图片匹配矩形、几何参数、Western/荧光/病理结构化证据和递归 JSON 数值证据通过稳定结果模型保存并供 UI/Excel 报告消费，消费者不读取检测器内部状态。

报告层通过共享中文标签、证据区域和只读缩略图工具输出三种格式：Excel 保存完整结构化明细，HTML 内嵌 CSS/JavaScript/PNG 并支持本地筛选，PDF 使用 ReportLab 和嵌入式系统中文字体形成 A4 归档版。具体决策见 `decisions/ADR-0004-local-html-pdf-reports.md`。

荧光和病理专项候选都使用分段倒排索引、单桶容量限制、每页特征上限和页对结果聚合，避免在数百至数千张输入时直接执行全部高成本两两验证。当前索引仍只存在于单次扫描内存，尚未实现断点恢复或历史库持久化。

Windows Alpha 免安装包使用 `pyside6-deploy`/Nuitka standalone 模式，由独立 GitHub Actions 工作流构建。选择与约束见 `decisions/ADR-0003-windows-portable-nuitka.md`。

## 尚未决定

- 持久化数据库及迁移框架。
- 局部特征的持久化索引和历史库近邻检索；当前倒排索引仅在单次扫描内存中建立。
- ONNX Runtime 或其他推理运行时的最终选择。
- 安装器实现；免安装包已建立原型但仍待干净 Windows 环境验证。

这些选择应通过独立 ADR 决定。
