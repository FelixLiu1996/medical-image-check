# 第三方组件与模型登记

当前尚未引入模型权重。以下开发依赖已经锁定，最终 Windows 发行物仍需按实际打包内容生成完整许可证清单。

## 准入原则

- 许可证必须明确，并与源码、Windows 二进制和模型包的实际分发方式兼容。
- 分别核查代码、模型实现、模型权重和必要数据资源的授权。
- 默认避免 Research Only、Non-Commercial、No Redistribution、No Derivatives、未知许可证及会改变核心发行义务的依赖。
- GPL、AGPL、LGPL、自定义模型许可证和带使用领域限制的条款必须单独评审。
- 必须保留要求的版权、许可证、署名和 NOTICE。

## 登记表

| 名称 | 版本 | 用途 | 来源 | 代码许可证 | 权重/资源许可证 | 是否打包 | 必要声明 | 审核状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PySide6 Essentials / Shiboken6 | 6.11.2 | Qt 桌面界面和部署工具 | https://doc.qt.io/qtforpython-6/ | LGPL-3.0-only OR GPL | 不适用 | 是 | 按 LGPLv3 动态分发，保留许可证、声明和替换能力 | 有条件接受，发行前复核 |
| openpyxl | 3.1.5 | xlsx/xlsm 只读解析 | https://openpyxl.readthedocs.io/ | MIT | 不适用 | 是 | 保留 MIT 许可证 | 已接受 |
| et-xmlfile | 2.0.0 | openpyxl 传递依赖 | https://foss.heptapod.net/openpyxl/et_xmlfile | MIT | 不适用 | 是 | 保留 MIT 许可证 | 已接受 |
| xlrd | 2.0.2 | xls 只读解析 | https://xlrd.readthedocs.io/ | BSD-style，多项声明 | 不适用 | 是 | 保留发行包内完整 LICENSE | 已接受 |
| NumPy | 2.5.2 | 图像数组、指纹和相似度计算 | https://numpy.org/ | BSD-3-Clause 及随包登记的宽松第三方许可证 | 不适用 | 是 | 自动收集 wheel 中 LICENSE 清单 | 已接受 |
| opencv-python-headless / OpenCV | 4.14.0.94 | 图片解码、多页 TIFF、缩放、DCT 和几何变换 | https://github.com/opencv/opencv-python | wheel 构建脚本 MIT；OpenCV Apache-2.0；wheel 含其他第三方组件 | 不适用 | 是 | 必须随包提供 `LICENSE.txt` 和 `LICENSE-3RD-PARTY.txt`；正式发行前复核 FFmpeg 等二进制声明 | 有条件接受，发行前复核 |
| ReportLab | 4.4.9 | 本地生成中文 PDF 报告 | https://www.reportlab.com/dev/docs/ | BSD-style | 不适用 | 是 | 保留发行包内 `LICENSE.txt` | 已接受 |
| Pillow | 12.3.0 | ReportLab 图像支持依赖 | https://python-pillow.org/ | MIT-CMU | 不适用 | 是 | 保留发行包内许可证 | 已接受 |
| charset-normalizer | 3.5.0 | ReportLab 文本编码依赖 | https://github.com/jawah/charset_normalizer | MIT | 不适用 | 是 | 保留发行包内许可证 | 已接受 |
| Nuitka | 4.1.1 | Windows standalone 构建 | https://nuitka.net/ | 编译器 AGPL-3.0；输出所含 Runtime Library 带 Nuitka Runtime Library Exception 1.0 | 不适用 | 仅构建工具；输出包含其 runtime | 随包提供 AGPL 文本和 Runtime Library Exception；该例外明确允许非 AGPL 目标程序按自选条款分发 | 有条件接受，发行前复核 |
| xlwt | 1.3.0 | 自动化测试中生成合成 xls | https://xlwt.readthedocs.io/ | BSD-style | 不适用 | 否 | 开发依赖 | 已接受 |
| pytest / pytest-cov | 9.1.1 / 7.0.0 | 自动化测试 | https://pytest.org/ | MIT | 不适用 | 否 | 开发依赖 | 已接受 |
| Ruff | 0.16.4 | 静态检查和格式化 | https://docs.astral.sh/ruff/ | MIT | 不适用 | 否 | 开发依赖 | 已接受 |

发行前应同时生成机器可读的模型清单，至少包含名称、版本、SHA-256、来源、许可证、算法兼容版本和推理后端。

Windows 免安装构建使用 `scripts/collect_licenses.py` 从实际安装的 Python 发行包收集许可证。由于 PySide6 wheel 元数据目前不携带完整许可证正文，仓库固定保存 GNU GPLv3 和 LGPLv3 原文，并在打包时复制到 PySide6 Essentials 和 Shiboken6 的许可证目录。
