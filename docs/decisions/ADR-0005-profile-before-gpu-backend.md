# ADR-0005：先建立性能画像，再选择 GPU 后端

- 状态：已接受
- 日期：2026-08-26

## 背景

CPU Alpha 已能完整执行图片和 Excel 查重。用户确认可用于首轮 GPU 验证的 Windows 电脑配备 NVIDIA GeForce RTX 3080 Ti，而主要开发电脑是 macOS ARM64，不能直接运行 NVIDIA CUDA。现有依赖 `opencv-python-headless` 的官方预编译 wheel 是 CPU-only；启用 OpenCV CUDA 需要定制构建，不能把当前依赖视为可直接切换的 CUDA 后端。

当前端到端扫描只记录完成时间，没有区分图片解码、通用特征、各专项特征、候选验证和 Excel 规则耗时。缺少这些数据时直接引入 CUDA，无法判断收益是否来自真实瓶颈，也难以控制包体积、驱动兼容、结果一致性和 CPU 回退风险。

## 决策

1. `0.1.0a12` 先建立 performance schema 1，不启用 GPU 计算，也不改变检测规则、阈值或算法版本。
2. 每次扫描记录总墙钟时间、排除暂停后的有效时间、暂停时间，以及稳定英文 ID 的分阶段耗时、调用次数和处理项数。
3. 运行环境记录操作系统、架构、CPU 逻辑核心、Python/OpenCV 版本；Windows 通过无 shell 的 `nvidia-smi` 查询记录 NVIDIA GPU 名称、驱动和显存，并探测 OpenCV CUDA 设备数。探测失败不得阻止扫描。
4. 当前执行后端固定记录为 `cpu`。即使检测到 NVIDIA GPU，也明确区分“硬件存在”和“OpenCV CUDA 后端可用”，不得把硬件存在误报为已经加速。
5. 性能画像随 schema 7 项目保存，并可从 GUI 导出 JSON 诊断。诊断不包含原始图片、表格路径、查重位置或结构化证据，便于试用用户回传。
6. RTX 3080 Ti 是首个参考 GPU，不是最低配置，也不意味着第一版只允许该型号。驱动版本和实际显存由诊断文件读取，不硬编码。
7. macOS 负责领域接口、CPU 回退、模拟探测和诊断测试；普通 Windows GitHub runner 继续验证 CPU、打包和无 GPU 启动。真实 CUDA 运行、显存和 CPU/GPU 结果一致性必须在 RTX 3080 Ti 或后续等效 NVIDIA 环境验证。
8. 收到同一输入的 CPU/RTX 性能画像后，再用独立 ADR 选择 CUDA 接入方式。第三方 CUDA wheel、自编译 OpenCV 或其他运行时在完成许可证、供应链、体积和无 GPU 启动审查前不得进入发行依赖。

## 理由

- 先测量可以优先加速图片特征或候选验证等真实热点，避免优化 Excel、文件 I/O 或低占比步骤。
- 稳定画像格式使 Mac、普通 Windows 和 RTX 机器结果可对照，也为后续 GPU 回退和性能验收提供证据。
- 保持 CPU-only 发行依赖可以在 GPU 方案未验证时继续稳定发布，不把 CUDA 驱动变成启动条件。

## 被否决的方案

- 直接将 `opencv-python-headless` 替换为来源未审查的 CUDA wheel：包来源、许可证、运行库、体积和无 NVIDIA 环境启动均未验证。
- 立即维护自编译 OpenCV CUDA：在没有阶段耗时前会增加较大构建成本，且无法证明 ORB/专项流程值得整体迁移。
- 只记录端到端总时间：不能区分解码、特征、召回、精查和 Excel 规则，不足以指导后端选择。
- 因开发机无 NVIDIA GPU 而停止 GPU 工作：接口、探测、CPU 回退、诊断和打包仍可在 Mac/普通 CI 完成，真实执行通过参考机闭环。

## 后果

- `0.1.0a12` 仍是 CPU 版本，不能宣称 CUDA 已启用。
- 项目 schema 升级为 7，并兼容读取 schema 1–6；旧扫描没有性能画像，需要用新版本重新扫描后才能导出诊断。
- 每次扫描增加一次轻量硬件探测和计时记录；失败只写入诊断，不产生扫描错误。
- GPU 后端开发必须同时提供 CPU/GPU 一致性、GPU 异常回退、Windows portable 和 RTX 实机结果。

## 参考

- https://github.com/opencv/opencv-python/blob/4.x/README.md
- https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- https://docs.github.com/en/actions/concepts/runners/self-hosted-runners
