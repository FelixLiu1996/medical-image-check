# ADR-0002：Python/PySide6 模块化单体

- 状态：已接受
- 日期：2026-08-25

## 背景

第一版需要 Windows 桌面界面、传统图像算法、本地深度模型、Excel 多格式读取、CPU/GPU 回退和可打包发行。团队需要优先快速验证算法，同时保持 UI、算法和持久化可独立测试。

## 决策

- 使用 Python 3.12 作为开发和发行基线。
- 使用 PySide6 Essentials/Qt Widgets 构建中文桌面界面，避免第一阶段引入 Qt Addons、QML 和 Qt WebEngine。
- 使用 `src` 布局的模块化单体，领域模型、应用服务、输入适配、算法引擎和 UI 分层。
- 使用 openpyxl 读取 xlsx/xlsm、xlrd 读取 xls、标准库读取 csv。
- 第一阶段使用标准库 JSON 持久化项目清单；正式历史库计划使用 SQLite，但需单独 ADR。
- 传统视觉计划使用不捆绑 Qt 的 OpenCV Python headless 包；本地模型计划使用 ONNX Runtime。两者在实际引入时单独锁版本并复核第三方声明。
- Windows 发行优先验证 Qt 官方 `pyside6-deploy`/Nuitka 路径，在 Windows GitHub Actions 上构建。
- 使用 pytest 测试，Ruff 负责静态检查和格式约束。

## 理由

- Python 的图像、数值和 Excel 生态更适合快速迭代科研算法。
- PySide6 是 Qt 官方 Python 绑定，提供 Qt Widgets 和官方桌面部署工具。
- 单一 Python 进程比 .NET UI + Python 服务减少 IPC、打包和故障恢复复杂度。
- 分层后核心算法不依赖 Qt，可以在无界面环境测试，也保留将来替换 UI 的空间。
- ONNX Runtime 的执行提供程序接口能够将 CPU 基线与不同硬件后端隔离。

## 许可证与发行约束

- 项目代码继续采用 Apache-2.0。
- PySide6 Essentials 社区版采用 LGPLv3/GPL 双许可；项目按 LGPLv3 条件动态分发 Qt 库，保留许可证、声明和用户替换库的能力，不静态链接。
- Windows 发行物必须在正式 Release 前进行一次独立许可证清单和替换性检查。
- 第三方依赖的实际版本和分发状态记录在 `THIRD_PARTY_NOTICES.md`。

## GPU 决策边界

CPU 路径先实现。GPU 通过运行时后端适配层接入，不在此 ADR 锁定 DirectML、WinML 或 CUDA。DirectML 目前仍受支持但已进入持续维护阶段，因此将在 Windows 原型中与其他后端比较后再决定。

## 被否决的方案

- .NET/WPF + Python 算法子进程：Windows UI 原生，但双运行时、IPC、任务恢复和安装复杂度更高。
- Rust/Tauri + Python：安装体积有优势，但技术栈和进程边界过多，不利于首版算法迭代。
- 纯 .NET：发布体验好，但医学图像研究算法和 Python 模型生态的接入成本更高。

## 后果

- Windows 发行必须在 Windows 环境构建和验证。
- 安装包会包含 Python 和 Qt 运行时，体积较大。
- 必须持续维护 LGPL 合规材料和第三方声明。
- 性能关键路径需要分析后下沉到 NumPy/OpenCV/ONNX，而不是使用纯 Python 循环。

## 参考

- https://doc.qt.io/qtforpython-6/
- https://doc.qt.io/qtforpython-6/deployment/index.html
- https://opencv.org/license/
- https://onnxruntime.ai/docs/execution-providers/
