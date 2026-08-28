# 医学图片逐对本地评测约定

## 目的

逐对评测用于回答两个问题：确定重复的图片能否被检出，确定不重复但外观相似的图片会不会误报。第一阶段覆盖 Western blot、免疫荧光、普通病理和细胞明场图；Dot blot 暂缓，不计入当前结果。

真实图片、论文附件、本机绝对路径和本地清单不得提交到公有仓库或 Release。仓库只保存评测器、格式说明和人工合成测试。

## 医学标注原则

- `positive`：医学人员或可靠来源明确指出存在同源复用区域。
- `negative`：医学人员确认不重复，或来自已做阴性筛查的材料。
- 不确定案例不得为了凑数量强行标记，也不得进入准确率计算。
- 标注应落到实际重复范围：整图、panel、泳道、单条带、同一视野、局部组织区域或其他局部区域。
- 单条带与双条带的差异不能直接排除局部复用；仍需比较双条带中的每一条及周围背景。
- 彩色标记框只用于定位。评测应优先裁取未画框原图；不得让相同框线成为程序命中的依据。

## 清单格式

清单使用本地 JSON，`schema_version` 为 `1`。图片路径可相对清单，也可为本机绝对路径；`source_group` 表示同一论文、同一数据来源或同一母图，同一来源组不得跨 train、validation 和 test。

```json
{
  "schema_version": 1,
  "images": [
    {
      "id": "paper-a-figure-2",
      "path": "images/figure-2.png",
      "split": "validation",
      "source_group": "paper-a"
    }
  ],
  "pairs": [
    {
      "id": "paper-a-wb-band-1",
      "first": "paper-a-figure-2",
      "second": "paper-a-figure-2",
      "first_region": [120, 80, 260, 70],
      "second_region": [540, 310, 240, 65],
      "expected": "positive",
      "modality": "western_blot",
      "western_single_band_enabled": true,
      "reuse_scope": "band",
      "label_source": "clinician_confirmed",
      "note": "不同实验标签下的局部条带"
    }
  ]
}
```

区域统一写为 `[x, y, width, height]`，坐标原点位于图片左上角。同一图片内比较必须同时提供两个不同区域。`modality` 可为 `generic`、`western_blot`、`dot_blot`、`fluorescence` 或 `pathology`；也可另设 `analysis_mode` 覆盖检测模式。

## 运行与解读

Windows PowerShell：

```powershell
.venv\Scripts\python.exe scripts\evaluate_image_pairs.py `
  C:\path\to\manifest.json --output C:\path\to\result.json
```

结果包含逐对证据、TP/FP/FN/TN、precision、recall、specificity，并按 split 和图片类型分别统计。正常的荧光跨通道关系和病理不同倍率关系不会当成阳性。

validation 结果用于发现问题和调算法；只有来源隔离、标签经医学确认且从未参与调参的 test 集，才能用于对外报告准确率。真实图片始终只读，评测器只在临时目录生成裁剪副本。

## 当前医学验证结论（2026-08-28）

- 当前 validation 有 39 对：26 对标记重复、13 对标记不重复，覆盖 Western blot、免疫荧光、普通病理和细胞明场；Dot blot 暂缓。
- 医学复核已确认 5 对原先漏报的 Western blot 均为重复，1 对原先误报的 beta-actin 为不重复；新版在同一批 11 对 Western 样本中全部判断正确。
- 单条带与双条带的差别不能直接排除重复，新版仍会比较双条带中的单条及其背景。
- 这批图片已经参与规则调整，不能称为独立准确率。荧光、病理和明场仍存在漏报，后续需要继续补样本。

## 医学复核者下一步

优先准备从未参与本轮调参、且与现有论文来源不同的 Western blot：明显重复阳性，以及外观相似但确认不重复的内参阴性。每一对只需记录“重复/不重复/不确定”、实际比较区域和判断理由；不确定样本保留观察，但不计入准确率。Western 独立验证完成后，再依次补充免疫荧光小区域重复、细胞明场和 HE/IHC 交叉比对案例。
