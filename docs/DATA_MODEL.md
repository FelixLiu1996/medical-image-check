# 数据模型初稿

状态：核心对象和可恢复项目清单已建立，历史库和项目包仍待实现。

## 当前实现

- `Project` 使用 `schema_version = 2`，包含 UUID、名称、时间、源路径、最近一次扫描结果和报告路径。
- `ProjectStore` 使用 UTF-8 JSON 和临时文件替换方式原子保存，不修改源数据。
- `Finding`、`EvidenceLocation`、`ScanIssue` 和 `ScanResult` 作为 UI 与引擎之间的稳定对象。
- Finding ID 根据规则与位置生成确定性指纹。
- `ScanResult` 记录算法版本和扫描完成时间；项目加载可恢复结果、问题、置信度、证据详情和人工复核状态。
- 版本 1 项目清单会在内存中迁移为版本 2，缺失的结果和报告字段使用空值；下一次保存写为版本 2。

输入路径变化会使最近扫描结果失效，防止导出与当前输入不一致的旧结果。当前 `.mic-project.json` 仍是开发阶段清单格式，不等同于 PRD 中最终的可移植项目包，也尚未缓存图片特征。

## 核心实体

- Project：项目标识、名称、版本、创建/更新时间、设置。
- Source：原始文件、路径、格式、大小、指纹、状态。
- ImageItem：图片、TIFF 页或面板及其原图坐标。
- WorkbookItem：Excel/CSV 文件、工作表及可扫描数值范围。
- Group：实验组、通道、视野、病理倍率等用户或自动分组。
- FeatureRecord：算法版本、特征类型、模型版本和索引位置。
- ScanTask：扫描阶段、参数、进度、资源、断点和错误。
- Finding：结果类型、规则、风险、分数和证据。
- FindingGroup：互相关联的多个对象组成的重复组。
- Review：确认重复、正常关联、误报、待定和备注。
- HistoryEntry：历史库来源、指纹、特征和复核状态。
- Report：格式、筛选范围、生成版本和文件位置。

## 原始数据原则

- Source 默认保存绝对路径和指纹，不修改文件。
- 可选归档模式保存只读副本。
- 项目结果必须能够判断源文件丢失、移动或内容变化。

## 项目包

项目包至少包含：

- Manifest：格式版本、软件版本、创建时间和内容清单。
- Project：项目设置和分组。
- Index：指纹、缩略图和可移植查重特征。
- Results：结果、证据和人工复核。
- Reports：用户选择包含的报告。
- Originals：仅完整项目包可选包含。
- Checksums：包内关键内容校验。

导入未知或较新格式版本时不得静默损坏数据，应拒绝并说明，或通过明确迁移流程处理。

## 版本

- `project_schema_version`
- `package_format_version`
- `history_schema_version`
- `algorithm_version`
- `model_version`
- `report_schema_version`

人工结论继承必须同时核对源指纹、对应区域和相关算法规则。
