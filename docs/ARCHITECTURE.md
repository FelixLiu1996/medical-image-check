# 架构初稿

状态：基础架构已实现，持续演进。

## 已选技术栈

- Python 3.12
- PySide6 Essentials / Qt Widgets
- openpyxl、xlrd 和标准库 csv
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

当前 `BasicScanService` 已贯通输入收集、图片文件指纹、Excel 数值解析、精确重复结果和 UI 展示。项目清单已经具备 JSON 原子保存，但尚未接入 UI。

## 尚未决定

- 持久化数据库及迁移框架。
- 图像索引和近邻检索实现。
- ONNX Runtime 或其他推理运行时的最终选择。
- 安装器与免安装包实现。

这些选择应通过独立 ADR 决定。
