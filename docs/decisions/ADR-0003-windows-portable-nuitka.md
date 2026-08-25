# ADR-0003：Windows 免安装包采用 pyside6-deploy/Nuitka standalone

- 状态：已接受，用于 Alpha 构建验证
- 日期：2026-08-25

## 背景

第一版需要让没有 Python、Qt 或开发环境的 Windows 10/11 x64 用户直接运行软件，并通过 GitHub Actions 形成可重复构建。项目还必须保留 PySide6/Qt 动态库和第三方许可证材料，便于后续完成 LGPL 合规复核。

## 决策

- 使用 PySide6 6.11.2 自带的官方 `pyside6-deploy` 工具。
- 使用该工具默认兼容的 Nuitka 4.1.1。
- 使用 `standalone` 目录模式，再压缩为免安装 ZIP；不先使用单文件模式。
- Windows 构建只在 Windows GitHub Actions 环境执行。
- 构建后运行打包程序的 `--smoke-test`，并生成 ZIP 的 SHA-256 文件。
- 发行目录包含项目 LICENSE、NOTICE、第三方登记表，以及从实际 Python 发行包元数据中收集的许可证文件。
- 当前工作流产生 Alpha 测试工件，不自动创建正式 GitHub Release。

## 理由

- standalone 目录保留动态库边界，比单文件自解压模式更容易检查 Qt 库和许可证材料，也便于排查缺失 DLL。
- `pyside6-deploy` 是当前 PySide6 官方部署入口，能够生成并保存可版本控制的构建参数。
- GitHub Actions 的 Windows 镜像可以验证真实目标平台，macOS/Linux 本地构建不能替代 Windows 发行验证。

## 约束与后续

- 正式发布前仍要在干净 Windows 10 和 Windows 11 机器上人工启动、扫描和导出报告。
- 必须复核 Qt LGPLv3 的最终材料、动态库可替换性和 OpenCV wheel 内第三方二进制声明。
- 安装程序另行选型；本 ADR 只决定免安装包原型。
- 构建依赖升级必须重新运行打包冒烟和许可证收集。

## 参考

- https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html
- https://doc.qt.io/qtforpython-6/deployment/deployment-nuitka.html
