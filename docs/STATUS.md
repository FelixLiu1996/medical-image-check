# 项目状态

最后更新：2026-08-25

## 已完成

- 多轮产品需求澄清。
- 第一版 PRD 初稿。
- 算法、架构、数据模型、UI、测试、发布和路线图初稿。
- 跨窗口交接与文档同步规则。
- 配置 GitHub 远程仓库 `origin`。
- 建立需求与治理文档的 Git 基线。
- 用户确认 PRD 0.1 并授权初版开发。
- 创建 `codex/v0.1-foundation`。
- 通过 ADR-0002 确定 Python/PySide6 Essentials 模块化单体。
- 建立领域、引擎、基础设施、服务和 UI 分层。
- 实现图片文件 SHA-256 重复检测。
- 实现 Excel 完整数值、数值行重复和四种表格格式读取。
- 实现项目清单原子保存与版本检查。
- 建立 Ruff、pytest 和 Windows/Linux GitHub CI。
- 将项目新建、打开、保存、另存为和未保存提醒接入桌面 UI。
- 项目清单升级到版本 2，保存并恢复最近扫描结果、提示和报告路径，兼容读取版本 1。
- 实现包含概览、查重结果、扫描提示和项目输入的基础 Excel 报告。
- 引入 NumPy 2.5.2 和 opencv-python-headless 4.14.0.94，并登记许可证。
- 实现解码像素指纹、跨格式像素重复、多页 TIFF 和整体感知近似候选。
- 实现旋转/翻转指纹、分段候选索引及标准化缩略图验证。
- 通过 ADR-0003 确定 Windows Alpha 免安装包使用 `pyside6-deploy`/Nuitka standalone。
- 建立 Windows 免安装 ZIP、打包冒烟、SHA-256 和第三方许可证收集工作流。

## 进行中

- 推送本轮开发变更并验证 Windows/Linux CI。
- 手动触发 Windows portable 工作流，验证真实 Windows 构建产物。

## 下一步

1. 完成并下载验证 Windows 免安装 Alpha 工件。
2. 实现局部关键点、裁剪/重叠候选与几何验证证据。
3. 增加图像并排预览、匹配区域和证据详情 UI。
4. 继续 Excel 数字片段、近似值和数值变换规则。
5. 确认验收数据来源并据此校准阈值。
6. 创建并评审基础开发 PR。

## 阻塞或待定

- 验收样例数据待用户向团队确认。
- 算法准确率与阈值待样例校准。
- GPU 后端和历史库仍需后续 ADR/验证。
- 当前全局感知阈值仅通过合成数据验证，不能作为准确率承诺。
- Windows 免安装包需要 Actions 构建和干净 Windows 10/11 实机验证。

## 明确未开始

- Windows 安装程序和正式 Release
- 局部图像、Copy-Move 和专项图像算法
- Excel 高级规律与完整报告
- 历史库和完整项目包

## 最新验证

- Python 3.12.13
- Ruff 检查与格式检查通过
- `pip check` 通过
- pytest 24 项通过
- Qt offscreen 启动通过
- `pyside6-deploy --dry-run` 配置解析通过
- 第三方许可证收集脚本本地通过
- Python wheel 构建与源码编译检查通过
- GitHub CI Windows/Linux 通过（run `32821382142`）

本轮新增代码尚待新的 GitHub CI 与 Windows portable run 验证；上面的 GitHub run 只代表上一基线。
