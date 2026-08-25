# 跨窗口交接

最后更新：2026-08-25

## 当前目标

在 `codex/v0.1-foundation` 持续完成通用图像、Excel 高级规则和证据复核的 Windows Alpha 闭环，再通过 PR 合并到 `main`。

## 当前状态

- 用户已确认 `docs/PRD.md` 0.1 并授权开始初版开发。
- 已建立跨窗口开发治理文档。
- 本地 Git 已绑定远程仓库 `origin`：`https://github.com/FelixLiu1996/medical-image-check.git`。
- 当前分支：`codex/v0.1-foundation`。
- ADR-0002 已选择 Python 3.12、PySide6 Essentials 和模块化单体。
- 已实现项目 UI 生命周期、扫描结果持久化、基础 Excel 报告、图片文件/解码像素重复、整体感知近似、局部几何重叠、Excel 精确数值/行重复和 CI。
- 项目清单已升级为 schema 2，并兼容读取 schema 1。
- 已建立 Windows portable 工作流和 ADR-0003，并通过 GitHub Windows runner 构建、冒烟及下载校验。
- 本地开发版本为 `0.1.0a3`，扫描算法版本为 `generic-image-local-1`；局部结果已接入双图证据预览。

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
- 局部图像裁剪/重叠基线已实现；单图 Copy-Move、多面板拆分和医学专项算法尚未实现。

## 下一步

1. 实现 Excel 不同单元格连续数字片段、近似值和数值变换规则。
2. 进入 Western blot、荧光和病理图专项算法。
3. 在干净 Windows 10/11 实机人工验证 GUI、项目保存、扫描和报告导出。
4. 通过 PR 合并阶段性 Alpha 基线。

## 验证状态

本地 macOS ARM64、Python 3.12.13：

- `ruff check`：通过。
- `ruff format --check`：通过。
- `pip check`：通过。
- `pytest`：28 项通过。
- Qt offscreen 启动冒烟：通过。
- 项目保存/恢复与 Excel 报告：通过合成集成测试。
- 解码像素、多页 TIFF、旋转及 JPEG 压缩候选：通过合成测试。
- `pyside6-deploy --dry-run` 和许可证收集：本地通过。
- Python wheel 构建与源码编译检查：通过。
- GitHub CI：Windows 完整测试与 Linux 核心测试通过（run `32825585656`）。
- Windows portable：Windows runner 构建、打包冒烟和工件上传通过（run `32825585609`）。
- portable 工件：下载后使用原始 `.sha256` 文件校验通过，主程序、Qt/OpenCV 运行库和许可证材料齐全。
- 局部算法：旋转/缩放/压缩裁剪、双裁剪部分重叠及无关图片负例合成测试通过；证据 UI 冒烟通过。

本轮 `0.1.0a3` 改动尚待新的 GitHub CI 与 Windows portable 工作流验证。
