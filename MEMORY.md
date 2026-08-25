# 跨窗口交接

最后更新：2026-08-25

## 当前目标

在 `codex/v0.1-foundation` 持续完成通用图像、Excel 高级规则、医学专项算法和证据复核的 Windows Alpha 闭环，再通过 PR 合并到 `main`。

## 当前状态

- 用户已确认 `docs/PRD.md` 0.1 并授权开始初版开发。
- 已建立跨窗口开发治理文档。
- 本地 Git 已绑定远程仓库 `origin`：`https://github.com/FelixLiu1996/medical-image-check.git`。
- 当前分支：`codex/v0.1-foundation`。
- ADR-0002 已选择 Python 3.12、PySide6 Essentials 和模块化单体。
- 已实现项目 UI 生命周期、扫描结果持久化、三种本地报告、图片文件/解码像素重复、整体感知近似、局部几何重叠、Western/荧光/病理专项，以及 Excel 精确/片段/近似/运算/结构/稳健线性/统计规则和 CI。
- 项目清单已升级为 schema 5，并兼容读取 schema 1/2/3/4；保存连续数字片段、Western blot 单条带开关及 Excel 容差、运算目标和连续风险阈值。
- 已建立 Windows portable 工作流和 ADR-0003，并通过 GitHub Windows runner 构建、冒烟及下载校验。
- 已实现 Western blot 明暗条带/面板候选、分段索引、结构/几何/背景/掩膜验证、同图 Copy-Move 和可选单条带低风险检测。
- 已实现荧光通道/合并角色、同视野配准、Merge 成分正常关系和同通道疑似复用基线。
- 已实现病理光密度组织形态、组织掩膜、多尺度局部匹配、不同倍率正常关系和同倍率疑似复用基线。
- GUI 与 Excel“图像证据”已统一展示 Western/荧光/病理证据；原始机器字段保持稳定，界面和报告使用中文标签。
- 已实现后台扫描协作式暂停、继续和取消；取消恢复扫描前结果，不保存本次部分结果，尚不支持跨进程断点。
- 已实现 Excel、单文件 HTML、中文 A4 PDF 三种报告；HTML 内嵌证据并支持搜索/风险筛选，PDF 优先嵌入系统中文字体。
- GUI 增加统一报告导出、聚焦匹配区域和复制证据摘要。
- ADR-0004 记录 HTML/PDF 方案；ReportLab、Pillow、charset-normalizer 已登记并加入 portable 许可证收集。
- 打包程序 `--smoke-test` 会实际生成并校验 Excel、HTML、PDF 三种临时报告，Windows portable 必须通过该路径。
- Excel 全局扫描已支持自定义相对/绝对容差、单次与连续四则运算、连续片段、乱序、少量修改、二维区域、Theil–Sen 稳健线性和固定低风险统计提示；GUI、项目恢复和三种报告已贯通参数与证据。
- 本地开发版本为 `0.1.0a8`，扫描算法版本为 `generic-image-local-1+western-blot-1+fluorescence-1+pathology-1+excel-advanced-2`。

## 已确认的重要方向

- Windows 10/11 x64 中文桌面软件。
- 面向基础医学实验研究团队，不考虑临床试验和医疗数据安全合规功能。
- 图像和 Excel 查重完全本地运行，不接入大模型 API。
- CPU 必须兼容，GPU 只用于加速。
- 第一版图像专项优先级：Western blot、荧光图、普通病理图。
- 第一版常规静态图片通用查重；不支持 PDF、Word、PPT、DICOM、超大切片和视频。
- Excel 支持 xlsx、xls、xlsm、csv，默认扫描全部工作表。
- 报告支持 Excel、HTML、PDF。
- GitHub 公有仓库，Apache-2.0。
- PySide6 按 LGPLv3 动态分发要求管理，最终发行前必须复核许可证材料。
- 通用图像基线使用 NumPy/OpenCV，CPU 完整运行；感知近似只输出中/低风险候选。
- Windows 免安装 Alpha 使用 `pyside6-deploy`/Nuitka standalone。Nuitka 编译器为 AGPLv3，目标程序 runtime 依赖其明确的 Runtime Library Exception，发行包必须保留该文本。

## 待定或阻塞

- 图像和 Excel 验收样例数据待用户向团队确认后提供。
- 算法准确率和最终阈值需依据验收数据校准。
- 历史库持久化和 GPU 后端仍需单独 ADR。
- Windows portable 已通过 GitHub Windows runner 构建与打包冒烟；干净 Windows 10/11 实机人工操作仍待验证。
- 局部图像裁剪/重叠和 Western/荧光/病理三个专项基线已实现；通用单图 Copy-Move、可调整多面板拆分、荧光实验组语义和病理连续切片语义尚未实现。
- Western blot 当前只形成明显横向条带行候选，复杂排版、弱条带、文字标签、任意擦除/拼接仍待验收数据和后续算法覆盖。
- Excel 已确认规则的全局扫描 Alpha 基线已实现；自动识别实验组、手动框选区域和人工复核结论/备注编辑尚未实现。新增结构/运算/统计规则每类最多保留 300 条，统计相似始终只标低风险。

## 下一步

1. 使用授权 Western/荧光/病理正负例调优阈值、实验组和连续切片边界。
2. 使用授权 Excel 正负例校准新增规则，并设计自动识别与手动框选扫描方式。
3. 设计持久化任务断点、崩溃恢复和历史库。
4. 在干净 Windows 10/11 实机验证 GUI、暂停/取消、三种报告和 PDF 字体/打印。

## 验证状态

本地 macOS ARM64、Python 3.12.13：

- `ruff check`：通过。
- `ruff format --check`：通过。
- `pip check`：通过。
- `pytest`：86 项通过。
- Qt offscreen 启动冒烟：通过。
- 项目保存/恢复与 Excel 报告：通过合成集成测试。
- 解码像素、多页 TIFF、旋转及 JPEG 压缩候选：通过合成测试。
- `pyside6-deploy --dry-run` 和许可证收集：本地通过。
- Python wheel 构建与源码编译检查：通过。
- GitHub CI：Windows 完整测试与 Linux 核心测试通过（run `32834918247`）。
- Windows portable：`0.1.0a5` 在 Windows runner 构建、打包冒烟和工件上传通过（run `32834918889`）。
- portable 工件：下载后使用原始 `.sha256` 文件校验通过，共 133 个条目，主程序、Qt/OpenCV 运行库和许可证材料齐全。
- 荧光/病理阶段 CI：Windows/Linux 通过（run `32840344162`）。
- Windows portable：`0.1.0a6` standalone 构建、程序冒烟、ZIP 组装和上传通过（run `32840344121`）；工件大小 85,370,794 字节。
- 局部算法：旋转/缩放/压缩裁剪、双裁剪部分重叠及无关图片负例合成测试通过；证据 UI 冒烟通过。
- Western blot：曝光变化/水平翻转、同图面板 Copy-Move、单条带开关、无关面板负例、项目迁移、GUI 和报告合成测试通过。
- 荧光：单通道与 Merge、不同通道同视野、同通道曝光变化复用、完全重复去重、GUI 和报告合成测试通过。
- 病理：同区域不同倍率、同倍率局部复用、无关组织负例、完全重复去重、GUI 和报告合成测试通过。
- Western 合成微基准：500 张随机条带图提取 542 个候选约 2.3 秒，验证约 31,900 对索引候选约 0.5 秒，峰值约 101 MiB，产生 1 条低风险候选。
- 荧光/病理内存合成微基准：300 张随机稀疏荧光图约 1.0 秒提取、0.1 秒索引验证；150 张随机染色形态图约 0.7 秒提取、0.02 秒索引验证；两组无关输入均无结果，不含文件解码和通用算法。
- 扫描控制：暂停/继续线程事件和首文件后取消的合成测试通过；UI 按钮和取消前结果保留路径已接入。
- HTML：单文件内嵌 PNG、搜索/风险筛选、无网络资源及源图不变测试通过。
- PDF：两页中文样例通过 Poppler 全页渲染目视检查，标题/结果/图像证据/扫描提示文本层经 pypdf 验证可检索；ReportLab/Pillow/charset-normalizer 许可证收集通过。
- 远程验收：GitHub CI Windows/Linux run `32844253484` 通过；Windows portable run `32844253505` 完成 standalone、打包 EXE 三报告冒烟、许可证收集和上传，工件 92.1 MB，artifact digest `63d43c1488b5798e28ceccc57185617c3e4ad4c688868658fdf8fc52e6068e74`。
- `excel-advanced-2` 合成测试覆盖自定义容差、四则运算、连续片段、乱序、少量修改、二维区域、稳健线性、汇总/分布统计、schema 5 和 GUI 参数恢复；80,000 个内存数值单元格压力检查约 3.3 秒、最大 RSS 约 95 MiB（不含读取、旧规则和报告）。
