# 数据模型初稿

状态：核心对象和可恢复项目清单已建立，历史库和项目包仍待实现。

## 当前实现

- `Project` 使用 `schema_version = 8`，包含 UUID、名称、时间、源路径、图片内容类型、复合图拆分开关及子面板选择、连续数字片段最短位数、Western blot 单条带开关、Excel 自定义相对/绝对容差、运算目标、连续风险阈值、最近一次扫描结果和 Excel/HTML/PDF 报告路径。
- `ProjectStore` 使用 UTF-8 JSON 和临时文件替换方式原子保存，不修改源数据。
- `Finding`、`EvidenceLocation`、`ScanIssue` 和 `ScanResult` 作为 UI 与引擎之间的稳定对象。
- Finding ID 根据规则与位置生成确定性指纹。
- `ScanResult` 记录算法版本、扫描完成时间和可选 performance schema 1；项目加载可恢复结果、问题、置信度、证据详情、人工复核状态和性能画像。
- 局部图像结果在 `Finding.details` 中记录两侧匹配矩形、覆盖率、关键点/内点统计、重投影误差、几何模型、尺度和旋转，供项目恢复、报告和证据 UI 共用。
- Western blot 结果记录两侧面板框、条带框、极性、翻转/旋转、条带结构、排列几何、背景纹理和掩膜重叠证据。
- Dot blot 结果记录两侧实际匹配框、检出/匹配斑点数与索引、归一化排列误差、排列/轮廓/局部图像相似度、最低单斑点相似度，以及裁剪、缩放、旋转、镜像和对比度变换参数。
- 荧光结果记录正常/疑似关系、通道角色、实际匹配通道、两侧区域、结构、前景掩膜、互信息、配准位移和变换。
- 病理结果记录正常/疑似关系、两侧组织区域、倍率、估算尺度比、组织占比、结构、组织掩膜、指纹距离和变换。
- `PanelSelection` 保存原图绝对路径、TIFF 页码、稳定子面板序号、`x/y/width/height` 原图坐标和勾选状态；不保存临时裁剪路径或像素副本。
- 版本 1–7 项目清单会在内存中迁移为版本 8；缺失的图片内容类型默认使用 `auto`，复合图拆分默认关闭且选择为空，其他缺失结果、报告、片段设置、单条带开关和 Excel 高级参数使用相应默认值，旧扫描的性能画像保持为空，下一次保存写为版本 8。
- performance schema 1 记录 CPU/GPU 运行环境、实际后端、加速器状态、墙钟/有效/暂停时间和稳定阶段 ID 的耗时、调用次数及处理项数。画像不记录源路径或结果证据；JSON 诊断另记录软件/算法版本和扫描数量统计。
- `Finding.details` 支持递归 JSON 证据；Excel 片段/近似结果保存逐单元格完整值，序列、区域、运算和统计关系保存逐位置配对值、关系结果、拟合或汇总参数。
- Excel `Finding.details.attention_tier` 使用 `primary/secondary/normal` 表示重点候选、次要线索和正常关系；同一列关系的等价规则使用 `relation_group_primary`、`related_rules` 关联。它们复用 schema 8 的递归证据，不再改变项目格式。读取阶段的公式文本只存在于内存，不写入项目、报告证据或反馈清单。

输入路径、图片内容类型、复合图拆分开关或选择、连续数字片段最短位数、Western blot 单条带开关或任一 Excel 高级参数变化会使最近扫描结果失效，防止导出与当前输入/参数不一致的旧结果。图片内容类型取 `auto/generic/western_blot/dot_blot/fluorescence/pathology` 之一。当前 `.mic-project.json` 仍是开发阶段清单格式，不等同于 PRD 中最终的可移植项目包，也尚未缓存图片特征。

扫描暂停/取消状态当前只存在于进程内，不写入项目清单。取消后的本次部分特征和部分结果全部丢弃，上一次完整 `ScanResult` 保持不变；崩溃恢复和跨进程任务断点尚未实现。

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
- `Finding.review_status`：`pending/confirmed/false_positive/normal`，分别对应未标记、准确、误报和正常关联；继续随扫描结果保存。
- Feedback export schema 1：Excel/JSON 清单只包含已标记结果，记录软件/算法版本、项目标识、结果 ID、规则、风险、位置和结构化证据；不复制原始文件。
- HistoryEntry：历史库来源、指纹、特征和可选反馈标记。
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
- Results：结果、证据和轻量反馈标记。
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

当前重扫仅在算法版本相同且 `finding_id` 完全一致时继承轻量反馈；输入或检测参数变化会先使旧扫描整体失效，算法版本变化也不会自动继承。未来若支持跨算法版本或历史库继承，还必须核对源指纹、对应区域和相关算法规则。
