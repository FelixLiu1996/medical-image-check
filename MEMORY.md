# 跨窗口交接

最后更新：2026-08-25

## 当前目标

在 `codex/v0.1-foundation` 完成可保存、可报告、可打包并具备通用图片候选层的 Windows Alpha 闭环，再通过 PR 合并到 `main`。

## 当前状态

- 用户已确认 `docs/PRD.md` 0.1 并授权开始初版开发。
- 已建立跨窗口开发治理文档。
- 本地 Git 已绑定远程仓库 `origin`：`https://github.com/FelixLiu1996/medical-image-check.git`。
- 当前分支：`codex/v0.1-foundation`。
- ADR-0002 已选择 Python 3.12、PySide6 Essentials 和模块化单体。
- 已实现项目 UI 生命周期、扫描结果持久化、基础 Excel 报告、图片文件/解码像素重复、整体感知近似、Excel 精确数值/行重复和 CI。
- 项目清单已升级为 schema 2，并兼容读取 schema 1。
- 已建立 Windows portable 工作流和 ADR-0003，等待目标平台构建验证。

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
- Windows portable 工作流和干净 Windows 10/11 运行尚未验证。
- 局部图像裁剪/重叠、Copy-Move 和医学专项算法尚未实现。

## 下一步

1. 推送本轮变更并确认 Windows/Linux GitHub CI。
2. 手动触发、下载并验证 Windows portable 工件。
3. 实现局部关键点、裁剪/重叠召回和几何验证。
4. 增加图像证据预览，并继续 Excel 高级数值规则。
5. 通过 PR 合并阶段性 Alpha 基线。

## 验证状态

本地 macOS ARM64、Python 3.12.13：

- `ruff check`：通过。
- `ruff format --check`：通过。
- `pip check`：通过。
- `pytest`：24 项通过。
- Qt offscreen 启动冒烟：通过。
- 项目保存/恢复与 Excel 报告：通过合成集成测试。
- 解码像素、多页 TIFF、旋转及 JPEG 压缩候选：通过合成测试。
- `pyside6-deploy --dry-run` 和许可证收集：本地通过。
- Python wheel 构建与源码编译检查：通过。
- GitHub CI：Windows 完整测试与 Linux 核心测试通过（run `32821382142`）。

本轮 24 项测试和打包改动尚未由新的远程 run 验证，旧 run 仅表示上一提交基线。
