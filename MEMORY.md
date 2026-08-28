# 跨窗口交接

最后更新：2026-08-28

## 当前目标

当前在 `codex/image-benchmark-v1` 整理首批真实医学图片 validation，并新增覆盖 Western blot、免疫荧光、普通病理和细胞明场的通用逐对本地评测器。Dot blot 按用户决定暂缓；真实图片、裁剪、清单和结果只保存在 Git 忽略目录。

## 当前状态

- 用户已确认 `docs/PRD.md` 0.1 并授权开始初版开发。
- 已建立跨窗口开发治理文档。
- 本地 Git 已绑定远程仓库 `origin`：`https://github.com/FelixLiu1996/medical-image-check.git`。
- 轻量反馈功能提交已快进合并并推送到 `main`（`2d9ea57`），开发分支和 main 必需 CI 均通过。
- GPU 性能画像已合入 `main`（`37bc7ca`），标签 `v0.1.0-alpha.12` 已创建；当前仍固定使用 CPU 后端。
- ADR-0002 已选择 Python 3.12、PySide6 Essentials 和模块化单体。
- 已实现项目 UI 生命周期、扫描结果持久化、三种本地报告、图片文件/解码像素重复、整体感知近似、局部几何重叠、Western/荧光/病理专项，以及 Excel 精确/片段/近似/运算/结构/稳健线性/统计规则和 CI。
- 项目清单已升级为 schema 7，并兼容读取 schema 1–6；除既有设置和结果外，可选保存 performance schema 1 性能画像，旧项目加载后性能画像为空。
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
- GUI 已重构为现代化中文首页、图片查重和数据查重两个独立工作区；直接文件、文件夹内容、参数、结果和证据均按模式隔离，项目保存为可选辅助功能。
- 扫描服务支持 `all/image/data` 工作区模式，图片或数据工作区会跳过另一套检测器；工作区模式不写入项目 schema，旧项目按已有输入和结果自动选择工作区。
- 图片内容类型支持 `auto/generic/western_blot/dot_blot/fluorescence/pathology`，自动模式收紧 Western blot/病理准入，明确模式只运行对应专项；通用检测始终运行。
- 新增 Dot blot 斑点排列专项和结构化证据，能处理裁剪、缩放、灰度/对比度差异；专项结果存在时折叠同图片对的通用候选。
- Excel 已增加全零/常量/身份运算/零乘积降噪、按证据强度排序以及跨工作簿/工作表公平选取；数据结果支持左右原工作表上下文和命中单元格高亮。
- 结果区已支持可选的准确/误报/正常关联标记、清除和状态筛选；标记随项目保存，同一算法版本的相同 `finding_id` 重扫时继承，不自动训练、上传或修改阈值。
- 已支持只导出已标记项的 Excel/JSON 算法反馈清单；包含算法版本、规则、位置和结构化证据，不复制原始文件。
- 功能分支统一使用 `<agent-name>/<feature-name>` 命名，例如 `codex/lightweight-review-feedback`。
- 开发分支触发 CI/打包后采用非阻断监控：主对话可继续；成功简要记录，失败必须反馈工作流、Job/步骤、关键错误、原因判断和建议修复，合并/标签/发布前确认最新提交的必需 run 全部成功。
- 已实现低侵入的扫描阶段计时和暂停时间扣除，覆盖输入收集、图片解码、通用/Western/Dot blot/荧光/病理特征与验证、Excel 读取和各规则阶段。
- 已实现只读运行环境探测：固定参数且不经过 shell 调用 `nvidia-smi`，并查询 OpenCV CUDA 设备数；探测失败不影响扫描，当前选择后端始终为 CPU。
- GUI 已增加“导出性能诊断”，JSON 包含软件/算法版本、机器/GPU 信息和阶段耗时，不包含原始文件、输入路径、查重位置或结构化证据。
- Excel 数值单元格读取已增加最近同列表头、表头行和只读公式引用元数据；序列按表格区块分段，明确编号段不进入数值规则。
- 同排公式或明确 `rescaled`/归一化表头支持的固定换算已分类为正常派生关系；全部规则仍保留，但数据界面默认展示跨规则归并、按来源配额且最多 50 条的重点候选，可切换全部线索。
- ADR-0006 已记录“完整线索 + 有界重点候选”策略；三种报告记录候选层级，HTML 支持层级筛选。
- 本地开发版本为 `0.1.0a15`，扫描算法版本为 `generic-image-local-1+western-blot-2+dot-blot-2+fluorescence-1+pathology-2+excel-advanced-4`。
- 2026-08-28 医学复核确认首轮 Western blot 的 5 个 FN 均为重复、唯一 FP（阴性论文第 15 页 G/H beta-actin）确为不重复；`western-blot-2` 增加窄条带行缩放/翻转回退和细长低覆盖通用证据过滤后，同一 11 对 validation 为 7 TP/0 FP/0 FN/4 TN。该结果不得作为独立准确率，下一步必须使用来源隔离的新样本验证。

## 已确认的重要方向

- Windows 10/11 x64 中文桌面软件。
- 面向基础医学实验研究团队，不考虑临床试验和医疗数据安全合规功能。
- 图像和 Excel 查重完全本地运行，不接入大模型 API。
- CPU 必须兼容，GPU 只用于加速。
- 首个真实 GPU 参考环境为 Windows 10/11 + NVIDIA GeForce RTX 3080 Ti；该型号不是最低配置。主要开发电脑是 macOS ARM64，不影响接口、CPU 回退和模拟探测开发，但不能替代真实 CUDA 验证。
- 第一版图像专项优先级：Western blot、荧光图、普通病理图。
- 第一版常规静态图片通用查重；不支持 PDF、Word、PPT、DICOM、超大切片和视频。
- Excel 支持 xlsx、xls、xlsm、csv，默认扫描全部工作表。
- 报告支持 Excel、HTML、PDF。
- GitHub 公有仓库，Apache-2.0。
- PySide6 按 LGPLv3 动态分发要求管理，最终发行前必须复核许可证材料。
- 通用图像基线使用 NumPy/OpenCV，CPU 完整运行；感知近似只输出中/低风险候选。
- Windows 免安装 Alpha 使用 `pyside6-deploy`/Nuitka standalone。Nuitka 编译器为 AGPLv3，目标程序 runtime 依赖其明确的 Runtime Library Exception，发行包必须保留该文本。
- 不建设强制逐条处理、审核完成率、审核人或复杂状态流转。轻量反馈仅提供“准确/误报/正常关联”一键标记，供本地离线算法调优；不自动训练、不自动改变阈值、不上传网络。

## 待定或阻塞

- 第一批图片 validation 已建立，但来源标记阳性仍需逐对医学确认，独立 test 集尚未形成。
- 算法准确率和最终阈值需依据验收数据校准。
- 历史库持久化仍需单独 ADR；GPU 已通过 ADR-0005 确认“先性能画像、后后端选择”，真正 CUDA 后端仍未实现。
- RTX 3080 Ti 参考机的驱动版本、`nvidia-smi` 探测结果和真实性能诊断尚待 Windows portable 实机回传。
- 三种导出报告是否增加“左右原表有限上下文 + 黄色命中高亮”仍待与试用用户讨论；当前仅 GUI 支持，Excel 报告输出结构化数值证据，HTML/PDF 输出位置和关系。确认支持范围、上下文大小和证据数量上限前不得开始开发。
- Windows portable 已通过 GitHub Windows runner 构建与打包冒烟；干净 Windows 10/11 实机人工操作仍待验证。
- 局部图像裁剪/重叠和 Western/荧光/病理三个专项基线已实现；通用单图 Copy-Move、可调整多面板拆分、荧光实验组语义和病理连续切片语义尚未实现。
- Western blot 当前只形成明显横向条带行候选，复杂排版、弱条带、文字标签、任意擦除/拼接仍待验收数据和后续算法覆盖。
- Excel 已确认规则的全局扫描 Alpha 基线已实现；自动识别实验组和手动框选区域尚未实现。重点队列是工程排序而非准确率结论；全部线索中统计相似始终只标低风险。
- Dot blot 已用局部内容验证拒绝暗底划痕和相同排列但内容不相容的合成负例，并稳定命中一侧 3 个斑点与另一侧 8 个斑点的局部子集；二维网格、任意稀疏子集和真实误报边界仍需更多逐对标签。

## 下一步

1. 医学人员逐对复核首轮阳性，未确认项继续留在 validation，不进入 test。
2. 使用来源隔离的新 Western 阳性和相似内参阴性验证 `western-blot-2`，不再围绕当前 11 对继续调参；随后处理细胞明场和荧光小区域漏报。
3. 补足来源隔离的 Western、HE/IHC 和免疫荧光正负例；Dot blot 暂缓，不阻塞第一阶段。
4. 建立未参与调参的 test 集后再报告准确率；RTX 3080 Ti 性能诊断仍与准确性优化分开。

## 验证状态

本次 Windows 开发分支验证：

- Python 3.12.10，`ruff check`、`ruff format --check` 和 `pip check` 通过。
- `QT_QPA_PLATFORM=offscreen` 下 pytest 123 项全部通过；新增通用图片逐对评测、窄条带行缩放/翻转和 Western 低覆盖细长局部证据过滤回归。
- 开发分支提交 `49c99f1` 已推送；GitHub CI run `33147098251`（Windows/Linux）和 Windows portable run `33147098252` 均成功。

本地 macOS ARM64、Python 3.12.13：

- `ruff check`：通过。
- `ruff format --check`：通过。
- `pip check`：通过。
- `pytest`：119 项通过；新增 Dot blot 弱斑点、3/8 局部子集、常见等间距排列/不相容内容负例和本地评测清单测试，并保留既有 Excel/GUI 回归。
- Qt offscreen 启动冒烟：通过。
- 项目保存/恢复与 Excel 报告：通过合成集成测试。
- 解码像素、多页 TIFF、旋转及 JPEG 压缩候选：通过合成测试。
- `pyside6-deploy --dry-run` 和许可证收集：本地通过。
- `0.1.0a9` Qt offscreen 首页、图片和数据工作区截图检查通过；图片页使用可滚动工作区避免低高度窗口挤掉导航。
- `0.1.0a11` 轻量反馈结果区 Qt offscreen 截图检查通过；wheel、打包程序三报告冒烟、许可证收集和 `pyside6-deploy --dry-run` 本地通过。
- `0.1.0a12` 在 macOS ARM64 完成真实小图片扫描画像：正确识别 Darwin/ARM64、CPU 后端和无 NVIDIA 环境，并生成阶段耗时；未使用或写入真实实验数据。
- `0.1.0a12` Qt offscreen 1024×720 与 1280×900 布局检查通过；wheel 构建、打包程序三报告冒烟、许可证收集、源码编译和 `pyside6-deploy --dry-run` 本地通过。
- 4 张真实同源 Dot blot 本地只读回归在 `dot-blot-2` 自动模式下输出全部 6 组关系且没有其他专项串类，无读取问题，约 1.8 秒；样例未入库。
- Dot blot 等间距负例合成微基准：25 张独立四斑点图片提取 28 个候选约 1.6 秒、验证约 2.3 秒，输出 0 条专项结果。
- Dot blot 开发分支提交 `9cd400c` 的 CI run `32989243824` 和 Windows portable run `32989243851` 均成功。
- 13 个真实 Excel 工作簿本地只读回归：`excel-advanced-4` 保留 1,775 条全部线索，识别 22 条正常派生关系，默认重点队列 50 条；MOESM9 数字片段、MOESM14 配对和为 2、MOESM15 结构重复仍在重点队列，约 6.36 秒；样例未入库。
- `0.1.0a13` PDF 新增候选层级列后完成两页 A4 Poppler 全页渲染目视检查，表头、分页和多行位置无裁切或重叠。
- `0.1.0a13` Qt offscreen 打包程序三报告冒烟、源码编译、wheel 构建和 `pyside6-deploy --dry-run` 通过。
- 双入口 GUI 源码 CI Windows/Linux run `32862204165` 通过；Windows portable run `32862205003` 完成 standalone、打包程序三报告冒烟、许可证收集、ZIP 组装和上传，总耗时 24 分 39 秒，工件 92.3 MB，artifact digest `9507b3b0998fb51cde1e48bdd1da1a880735ffd38a1dbe1583a47652edd7fd23`。
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
- Excel `0.1.0a8` 最新源码 CI Windows/Linux run `32849587649` 通过；Windows portable run `32849587674` 完成 standalone、程序三报告冒烟、许可证收集、ZIP 组装和上传，工件 92.3 MB，artifact digest `4b395721340e286b9ae278c58c0ee5c5292ca2d68b1c9290c7bd6aa12cf21083`。
