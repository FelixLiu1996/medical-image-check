# Medical Image Check 开发约定

## 当前阶段

项目目前处于需求评审阶段，尚未进入功能开发。未经用户明确确认 `docs/PRD.md` 并授权开始开发，不得擅自实现产品功能或锁定技术栈。

## 新窗口必读顺序

1. `AGENTS.md`
2. `MEMORY.md`
3. `docs/STATUS.md`
4. `docs/PRD.md`
5. 与当前任务相关的架构、算法、数据模型、UI、测试或 ADR 文档
6. Git 工作区状态和最近提交

聊天记录不是项目事实的唯一来源。仓库文档、代码、测试和 Git 状态应足以让新窗口继续工作。

## 单一事实来源

- 产品范围与验收依据：`docs/PRD.md`
- 当前进度与阻塞项：`docs/STATUS.md`
- 跨窗口交接摘要：`MEMORY.md`
- 已确认技术决策：`docs/decisions/`
- 用户可见变更：`CHANGELOG.md`
- 算法设计与阈值：`docs/ALGORITHMS.md`

若文档冲突，优先级为：已确认 PRD/ADR > 专项设计文档 > STATUS > MEMORY > README。发现冲突时必须同步修正文档。

## 不可违反的产品约束

- 第一版是 Windows 10/11 x64 中文桌面软件。
- 核心查重必须完全本地运行，不依赖大模型 API 或其他云端服务。
- 无 GPU 时所有核心功能必须可用；GPU 只用于加速。
- 不修改、删除或覆盖用户原始图片和 Excel。
- 第一版只检测、展示和导出结果，不自动删除重复内容。
- 查重结果是科研复核候选证据，不能自动等同于学术不端结论。
- 第一版专项图像算法优先覆盖 Western blot、荧光图和普通病理图片。
- 不向公有仓库提交真实实验数据、项目包、密钥、证书或受限制模型权重。
- 第三方代码、模型和数据资源必须完成许可证核查后才能进入发行包。

## 变更流程

开始修改前：

1. 检查 `git status`，不得覆盖用户已有修改。
2. 确认需求来源和目标版本。
3. 阅读相关设计及 ADR。
4. 对重要或不可逆技术选择先新增 ADR。

完成修改前：

1. 运行与风险相称的测试和静态检查。
2. 更新受影响的需求、架构、算法、数据模型或测试文档。
3. 更新 `docs/STATUS.md` 和 `MEMORY.md`。
4. 有用户可见变化时更新 `CHANGELOG.md` 的 `Unreleased`。
5. 记录未完成项、已知问题和准确的下一步。

## 文档同步规则

| 变更类型 | 必须检查或更新 |
| --- | --- |
| 功能范围 | PRD、ROADMAP、STATUS、CHANGELOG |
| 算法或阈值 | ALGORITHMS、TESTING、ADR、CHANGELOG |
| 项目包或数据库格式 | DATA_MODEL、ARCHITECTURE、迁移测试、CHANGELOG |
| UI 流程 | UI_UX、PRD、相关测试 |
| 构建与发布 | README、RELEASE、GitHub workflow、CHANGELOG |
| 第三方依赖或模型 | THIRD_PARTY_NOTICES、依赖锁文件、RELEASE |

## Git 和提交

- 功能分支默认使用 `codex/<topic>`。
- 推荐 Conventional Commits：`feat:`、`fix:`、`docs:`、`test:`、`refactor:`、`build:`、`chore:`。
- 采用语义化版本。
- 不使用破坏性 Git 命令处理不属于当前任务的修改。

## 构建和测试命令

技术栈尚未确定，因此当前没有构建命令。技术栈确定后，必须在此处和 `README.md` 同步加入可复制执行的安装、运行、测试和打包命令。
