# 跨窗口交接

最后更新：2026-08-25

## 当前目标

在 `codex/v0.1-foundation` 完成首个可测试的 Windows 桌面基础闭环，并通过 PR 合并到 `main`。

## 当前状态

- 用户已确认 `docs/PRD.md` 0.1 并授权开始初版开发。
- 已建立跨窗口开发治理文档。
- 本地 Git 已绑定远程仓库 `origin`：`https://github.com/FelixLiu1996/medical-image-check.git`。
- 当前分支：`codex/v0.1-foundation`。
- ADR-0002 已选择 Python 3.12、PySide6 Essentials 和模块化单体。
- 已实现中文桌面骨架、基础扫描服务、图片文件精确重复、Excel 精确数值/行重复、项目清单持久化和 CI。

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

## 待定或阻塞

- 图像和 Excel 验收样例数据待用户向团队确认后提供。
- 算法准确率和最终阈值需依据验收数据校准。
- 历史库持久化和 GPU 后端仍需单独 ADR。
- Windows 发行打包尚未验证。

## 下一步

1. 推送开发分支并确认 Windows/Linux GitHub CI。
2. 通过 PR 合并基础工程。
3. 将项目清单接入 UI，并增加基础 Excel 报告。
4. 实现解码像素指纹与通用图像近似候选层。

## 验证状态

本地 macOS ARM64、Python 3.12.13：

- `ruff check`：通过。
- `ruff format --check`：通过。
- `pip check`：通过。
- `pytest`：15 项通过。
- Qt offscreen 启动冒烟：通过。
- Python wheel 构建与源码编译检查：通过。
- GitHub CI：Windows 完整测试与 Linux 核心测试通过（run `32821382142`）。
