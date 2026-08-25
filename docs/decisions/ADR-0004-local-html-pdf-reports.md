# ADR-0004：HTML 单文件与 ReportLab PDF 报告

- 状态：已接受，用于 Alpha 报告基线
- 日期：2026-08-25

## 背景

PRD 要求第一版同时支持 Excel、HTML 和 PDF 报告。报告必须完全本地生成，能够展示结构化结果和图像证据，不依赖浏览器联网、云端字体或外部静态资源，并能进入 Windows 免安装包。

## 决策

- 保留 openpyxl Excel 报告作为完整结构化结果和二次统计格式。
- HTML 使用 Python 标准库生成单文件，CSS、JavaScript 和 PNG 证据缩略图全部内嵌；支持文本搜索和风险筛选，不引用网络资源。
- PDF 使用 ReportLab 4.4.9 生成 A4 归档报告，包含总览、完整结果表、图像证据、扫描提示和页码。
- PDF 在 Windows 优先发现并嵌入微软雅黑、宋体或等价系统中文字体；macOS/Linux 使用已知中文字体候选。无可嵌入字体时保留 ReportLab CID 回退，正式 Windows 发行以系统字体路径为验收对象。
- 系统字体只在报告生成时读取并嵌入 PDF 子集，不复制到应用发行包。
- 为控制报告体积，HTML 最多内嵌前 120 条双图像证据，PDF 最多内嵌前 40 条；所有结果仍完整保留在结果表和 Excel 报告。
- 三种报告均使用临时文件替换目标文件，并保持原始实验文件只读。

## 理由

- 单文件 HTML 便于跨电脑打开和归档，避免资源目录丢失，也符合完全离线要求。
- ReportLab 使用宽松 BSD-style 许可证，可合法进入 Apache-2.0 公有项目的 Windows 发行包；Pillow 和 charset-normalizer 传递依赖同样为宽松许可证。
- 系统中文字体嵌入避免依赖 PDF 查看器的 Adobe-GB1 语言包；视觉验收已验证中文、表格、图片和文本层。
- 明确证据上限可以防止数百、数千图片项目生成体积和内存不可控的报告。

## 约束与后续

- 当前报告导出全部结果，尚未实现“仅导出当前筛选结果”。
- HTML 支持搜索与风险筛选，但尚未实现叠加、闪烁和原图交互缩放。
- PDF 是归档版，不承载交互控件；超出图片证据上限时应结合 HTML/Excel 查看。
- 正式发行前必须在 Windows 10/11 验证微软雅黑/宋体发现、字体嵌入、打印和不同 PDF 阅读器显示。
- ReportLab、Pillow 和 charset-normalizer 的许可证文件必须由打包工作流从实际 wheel 收集。

## 参考

- https://docs.reportlab.com/
- https://opensource.org/license/bsd-3-clause
