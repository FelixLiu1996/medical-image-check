# Dot blot 本地评测约定

## 目标

使用逐对正负标签衡量 Dot blot 专项的检出和误报，不把“程序返回多少条结果”当作准确率。真实图片只保存在研究团队本机；仓库不提交图片、论文附件、绝对路径或可追溯到受限制数据的内容。

PubPeer 仅作为人工发现线索的来源，不进行自动抓取。公开论文附件能否进入评测集，需逐项确认原始发布站点的下载权限、许可证和再分发条件；没有明确再分发许可时只能在本地使用，不能进入仓库或 Release。

## 清单格式

本地 JSON 清单使用 `schema_version: 1`。图片路径可相对清单文件，也可为本机绝对路径。`source_group` 表示同一论文、同一数据来源或同一合成母图；同一来源组不得跨越 train/validation/test，避免变换后的同源图泄漏到不同集合。

```json
{
  "schema_version": 1,
  "images": [
    {
      "id": "paper-a-panel-1",
      "path": "images/panel-1.png",
      "split": "test",
      "source_group": "paper-a"
    },
    {
      "id": "paper-a-panel-1-crop",
      "path": "images/panel-1-crop.png",
      "split": "test",
      "source_group": "paper-a"
    }
  ],
  "pairs": [
    {
      "first": "paper-a-panel-1",
      "second": "paper-a-panel-1-crop",
      "expected": "positive",
      "minimum_matched_spots": 3,
      "note": "原图中的三个斑点被裁剪、缩放并调整对比度"
    }
  ]
}
```

`expected` 只能是 `positive` 或 `negative`。正例表示两张图确有同源复用区域，负例表示结构相似但来源不同或没有对应区域；未知关系不要放入评测对。标签应尽量记录来源证据，不能只根据当前程序输出反推。

## 运行

在仓库根目录和已安装开发环境中执行：

```bash
.venv/bin/python scripts/evaluate_dot_blot.py /path/to/manifest.json \
  --output /path/to/result.json
```

评测器只读取清单和原图片，调用显式 Dot blot 模式，输出 TP、FP、FN、TN、precision、recall、specificity、逐对证据和读取问题。显式模式会绕过 `dot-blot-4` 的 AUTO 页面准入及 AUTO 候选预算，因此该清单衡量的是 Dot 专项验证器本身，不能代替默认自动识别链路的路由评测；AUTO 应另用来源隔离的通用逐对清单同时标注“是否应进入 Dot 路由”和最终关系。它不会复制图片、联网、训练模型或修改算法阈值。Windows 将解释器路径替换为 `.venv\\Scripts\\python.exe`。

## 调优纪律

1. 先固定 test 标签；阈值只依据 train/validation 修改，test 用于阶段性验收。
2. 正例至少覆盖完整复用、3/8 等局部子集、弱斑点、裁剪、缩放、旋转、镜像和对比度变化。
3. 负例至少覆盖常见等间距阵列、不同斑点内容、矩形/文字/划痕、荧光或显微纹理、Western blot 条带和完全无关图片。
4. 若验收默认 AUTO，还应覆盖正式论文中的流程图节点、坐标轴/柱状图、病理组织纹理、Western 主导页、大图内小 Dot 阵列和 Western+Dot 混合页面；记录最长边 512 像素的常规 P12/P25/P55 门控及仅服务严格小斑点路径的 P10 层是否正确，并确认常规短长轴比/圆度阈值 0.70/0.65、共享基线柱条排除，以及 Western 单块覆盖至少 35% 或多块总覆盖至少 35% 且联合覆盖至少 25% 的主导页避让。还应确认两个零散小 Western 区域不会误屏蔽同页 Dot，候选预算会先覆盖不同页面对，避免单一密集页对先耗尽配额。显式 Dot blot 指标与 AUTO 端到端指标必须分开报告。
5. 报告总体指标的同时保留逐来源组指标和失败案例；同一来源产生的多个增强图不能当作多个独立真实样本宣传。
6. 每次修改阈值都更新 `docs/ALGORITHMS.md`、`docs/TESTING.md` 和变更日志，并保留可复现的合成回归。
