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
- 实现 ORB 局部描述子候选索引、双向比率匹配及 RANSAC 仿射/单应验证。
- 实现裁剪、大图包含小图和两张裁剪图部分重叠检测，输出两侧区域与几何参数。
- 在 GUI 中增加双图并排证据预览、匹配矩形和几何证据摘要。

## 当前阶段结论

- 本轮代码已推送到 `codex/v0.1-foundation`。
- Windows/Linux CI 已通过。
- Windows portable 工作流已在 GitHub Windows runner 上完成真实构建和打包冒烟。
- 已下载工件并直接验证 SHA-256；ZIP 包含主程序、Qt/OpenCV 运行库及第三方许可证材料。
- 本地扫描算法升级为 `generic-image-local-1`，软件版本升级为 `0.1.0a3`。

## 下一步

1. 实现 Excel 不同单元格之间连续数字片段规则。
2. 实现 Excel 近似值、固定倍数/偏移及数值运算关系。
3. 进入 Western blot、荧光和病理图专项算法。
4. 确认验收数据来源并据此校准全局/局部图像阈值。
5. 在干净 Windows 10/11 实机完成人工 GUI 与样例扫描验收。
6. 创建并评审基础开发 PR。

## 阻塞或待定

- 验收样例数据待用户向团队确认。
- 算法准确率与阈值待样例校准。
- GPU 后端和历史库仍需后续 ADR/验证。
- 当前全局感知和局部几何阈值仅通过合成数据验证，不能作为准确率承诺。
- Windows 免安装包已通过 Actions 构建与冒烟，仍需干净 Windows 10/11 实机人工验收。

## 明确未开始

- Windows 安装程序和正式 Release
- 单图 Copy-Move、多面板拆分和专项图像算法
- Excel 高级规律与完整报告
- 历史库和完整项目包

## 最新验证

- Python 3.12.13
- Ruff 检查与格式检查通过
- `pip check` 通过
- pytest 28 项通过
- Qt offscreen 启动通过
- `pyside6-deploy --dry-run` 配置解析通过
- 第三方许可证收集脚本本地通过
- Python wheel 构建与源码编译检查通过
- 2000 项×96 描述子的局部候选层合成微基准约 2.9 秒、进程峰值 RSS 约 304 MiB（不代表端到端扫描）
- GitHub CI Windows/Linux 通过（run `32825585656`）
- Windows portable 构建、打包冒烟和工件上传通过（run `32825585609`）
- 下载后的 portable ZIP 通过原始 `.sha256` 文件校验；主程序、Qt/OpenCV 运行库和许可证材料齐全

本轮局部算法与证据 UI 已完成本地验证，尚待新的 GitHub CI 和 Windows portable run 验证。
