# 公众号论文图片标注数据链路可行性与交接

- 状态：首篇试点已验证主要链路，尚未完成精确区域映射，未进入产品范围或正式开发
- 日期：2026-08-28
- 用途：记录首篇试点结果，并供新窗口继续主文 Figure 的精确标注；Supplement 仅作为后续可选扩展

## 1. 目标与边界

本次讨论的目标不是把公众号或论文检索接入软件的在线查重入口，而是建设一条本地训练与评测数据生产链路：利用科研诚信类公众号文章中已经标出的疑似图片重复关系，定位对应论文及干净原图，形成可追溯的正负样本。

该方向尚未写入 `docs/PRD.md`，也没有开始实现。若后续要成为正式开发范围，必须先同步 PRD、ROADMAP、STATUS、CHANGELOG，并视技术与数据许可选择补充 ADR。

## 2. 已达成的核心结论

### 2.1 三类来源承担不同职责

- 公众号、PubPeer 等公开标注：提供真实重复案例、可疑区域、Figure/Panel 编号和变换关系，是标签线索来源。
- PubMed、Crossref：用于解析论文身份、DOI、PMID、作者、期刊和年份，不作为主要图片文件来源。
- PMC Open Access、出版社开放附件、BioImage Archive 等：在许可证允许时提供无红框、无箭头的干净论文图片或实验图像，是训练图片资产来源。

公众号不应仅被视为“选题线索”。像 Figcheck 这类文章会直接圈出重复图片、重复区域和细节，因此可以显著降低寻找真实阳性的成本。但公众号标注图不能直接作为模型输入，否则算法可能学习红框、箭头和相同压缩痕迹，而不是图像复用本身。

### 2.2 标注与干净原图必须分离

推荐处理方式：

1. 保留公众号正文和标注截图作为本地证据。
2. 从正文提取论文标题、DOI、PMID、Figure/Panel 和重复关系。
3. 从许可明确的原始发布来源获取干净 Figure。
4. 将公众号标出的区域重新映射到无标记原图。
5. 使用干净原图裁出样本，公众号截图只用于定位和追溯。

这与 `docs/IMAGE_PAIR_EVALUATION.md` 的现有约定一致：彩色标记框只能用于定位，不得成为程序命中的依据。

### 2.3 默认负例规则

考虑到逐对证明所有图片“绝对不重复”的成本不可接受，初期采用工程化闭世界假设：

- 公众号或 PubPeer 明确指出的重复图片对作为正例。
- 未被公开标出的图片对默认作为负例，参与训练和初步评测。
- 标注范围不清楚，例如只说“Figure 3 有问题”但没有指出具体两个 Panel，则暂时标为 `unknown`，不能强行生成正负标签。
- 当算法命中某个默认负例时，只复核这个冲突样本；确认重复则改为正例，确认不重复则保留为困难负例，无法判断则移出计分集合。

默认负例在训练时按普通负类使用，不要求降低权重；但必须保留来源元数据，例如：

```json
{
  "expected": "negative",
  "label_source": "unreported_assumption",
  "review_status": "not_reviewed"
}
```

对外描述指标时必须明确：指标基于公众号及 PubPeer 的公开标注构建，未被公开标注的图片对默认视为负例。若算法发现此前未报道的真实重复，初始统计可能将其计为误报，完成复核和标签修正后再重新计算。

### 2.4 数据隔离

- `source_group` 至少按论文、同一数据来源或同一母图建立。
- 同一 `source_group` 不得跨越 train、validation 和 test。
- 同一篇论文的原图、裁剪图、标注图、增强图和不同版本必须位于同一 split。
- 跨论文仍应通过文件哈希、感知指纹和候选匹配检查潜在同源泄漏。
- 训练中使用的论文不能再作为独立 test 来源。

## 3. 推荐端到端流程

```text
公众号文章链接或本地文章文件
  -> 获取公开正文和 data-src 图片
  -> 提取公众号文章元数据及重复标注
  -> 解析论文标题、DOI、PMID/PMCID
  -> 核查撤稿/更正记录、许可证和图片单独版权
  -> 从许可明确的来源取得主文 Figure 和 Supplement
     -> 校验实际字节、格式、哈希和附件内容
     -> 验证页/验证码/企业策略阻塞则记录并走合规回退
  -> 匹配 Figure/Panel 并迁移重复区域
  -> 已标注关系生成正例
  -> 未标注关系默认生成负例
  -> 冲突样本进入人工复核
  -> 按 source_group 隔离数据集
  -> 输出本地训练/评测清单、许可证记录和处理日志
```

首版应是 Excel 驱动的半自动内部流程，不需要先开发 GUI。先处理 5 至 10 篇代表性文章，确认页面解析、论文定位、图片映射和许可证记录格式稳定，再批量处理约 100 篇。重复劳动稳定后再沉淀为命令行批处理工具。

## 4. 建议输入 Excel

最少只要求公众号文章链接，建议字段如下：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `case_id` | 建议 | 本地稳定案例编号 |
| `wechat_url` | 是 | 具体公众号文章链接，不是公众号名称 |
| `known_paper_title` | 否 | 已知论文标题 |
| `doi` | 否 | 已知 DOI |
| `notes` | 否 | 人工提示或重点关系 |
| `priority` | 否 | 处理优先级 |

处理后可回填文章读取状态、论文标识、Figure/Panel、重复关系、干净原图状态、许可证、样本数量、split、异常原因和人工复核状态。

## 5. 建议本地数据记录

每个案例至少保存：

- 公众号 URL、公众号名称、文章标题、发布时间和取得时间；
- 公众号正文和原始标注图片；
- 公众号对重复关系的文字描述；
- 论文标题、作者、期刊、年份、DOI、PMID、PMCID；
- Figure/Panel 编号和两侧区域坐标；
- 裁剪、缩放、旋转、翻转、调色、遮挡等疑似变换；
- 干净原图地址、文章许可证、图片单独版权说明；
- 文件 SHA-256、感知指纹和文章版本；
- `expected`、`label_source`、`review_status`、判断理由；
- `source_group` 和 train/validation/test 分组；
- 下载原始 URL、最终 URL、HTTP 状态、预期/实际内容类型、文件格式校验和 SHA-256；
- 获取失败、验证页、验证码、企业策略拦截、映射失败、许可证不清楚等问题。

真实文章、图片、裁剪、清单和结果只保存在 Git 忽略的本地数据目录。除非逐项确认允许再分发，否则不得进入公有仓库或 Release。

## 6. 许可证原则

- 首批自动准入建议限制为 CC0 和 CC BY。
- CC BY-SA、CC BY-NC、CC BY-ND、自定义条款及无明确许可证的内容必须单独评审。
- 即使论文整体开放，也必须检查具体 Figure 是否注明“转载自其他来源”“经许可使用”或存在单独版权；这类图片默认排除。
- Sci-Hub 不作为图片资产来源，不进入工具依赖或数据溯源链路。
- 公众号标注截图与正文的保存、内部使用和再分发条件应与原论文图片许可证分开记录。
- 公开模型权重、公开数据集和内部研究使用是不同的使用场景，进入发布阶段前必须重新完成许可证审查。

当前可参考的官方数据接口：

- PubMed E-utilities：https://www.ncbi.nlm.nih.gov/books/NBK25497/
- Crossref REST API：https://www.crossref.org/documentation/retrieve-metadata/rest-api/
- PMC AWS Article Datasets：https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/
- BioImage Archive：https://www.ebi.ac.uk/bioimage-archive/

## 7. 微信公众号公开文章获取验证

### 7.1 已知可解析结构

首篇试点已经使用公开 HTTPS 请求成功读取微信公众号单篇文章，并验证以下常见页面结构：

- 标题：`#activity-name`
- 公众号名称：`#js_name` 或 `.rich_media_meta_nickname`
- 正文：`#js_content`
- 正文图片：优先读取 `data-src`，部分页面回退到 `src`
- 页面 JavaScript 变量可包含发布时间、`biz` 和账号标识等公开元数据

#### 首选实现方法

新窗口验证公开单篇文章时，首选方法不是 Chrome 浏览器自动化，而是本地终端中的只读 HTTP 获取：

- Python 3.12；
- `requests` 发送普通 `GET` 请求，设置移动端微信或常规浏览器 `User-Agent`、连接/读取超时并允许正常重定向；
- `BeautifulSoup` 使用 `html.parser` 解析返回 HTML；
- 不登录微信，不使用 Cookie、验证码、公众号后台、私有接口或会话令牌；
- 只读取用户明确提供的单篇公开文章 URL，不自动遍历公众号历史文章；
- 若运行环境明确阻止该地址，不得改换通道绕过，改为读取用户导出的 HTML/PDF/Word/图片包。

最小读取示例：

```python
from __future__ import annotations

from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 Mobile/15E148 MicroMessenger/8.0.50"
    )
}


@dataclass(frozen=True)
class PublicArticle:
    final_url: str
    status_code: int
    raw_html: bytes
    title: str
    account: str
    body_text: str
    image_urls: tuple[str, ...]


def read_public_article(url: str) -> PublicArticle:
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=(10, 30),
        allow_redirects=True,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.content, "html.parser")
    content = soup.select_one("#js_content")
    if content is None:
        raise ValueError("页面缺少 #js_content，可能不是完整公开文章")

    title_node = soup.select_one("#activity-name")
    account_node = soup.select_one("#js_name, .rich_media_meta_nickname")
    image_urls = tuple(
        url_value
        for image in content.select("img")
        if (url_value := image.get("data-src") or image.get("src"))
    )

    return PublicArticle(
        final_url=response.url,
        status_code=response.status_code,
        raw_html=response.content,
        title=title_node.get_text(" ", strip=True) if title_node else "",
        account=account_node.get_text(" ", strip=True) if account_node else "",
        body_text=content.get_text("\n", strip=True),
        image_urls=image_urls,
    )
```

该示例只负责取得原始 HTML、完整正文和图片 URL，不包含批量抓取、图片重编码、论文检索或训练样本生成。若把 `requests`、`beautifulsoup4` 引入仓库开发依赖，必须同步依赖约束和许可证登记；一次性验证也可以在独立临时环境运行。

推荐的单篇文章获取步骤：

1. 使用当时安全策略允许的 HTTPS 客户端请求用户给出的完整公开文章 URL，设置合理超时并允许正常重定向；不得使用登录 Cookie、公众号后台凭据或绕过验证页面。
2. 检查最终状态为成功响应、内容确为 HTML，且页面中存在 `#js_content`；如果返回验证页、错误页或空正文，立即标记失败，不把错误页面当成文章。
3. 在修改或清洗前先原样保存响应 HTML，并记录原始 URL、最终 URL、HTTP 状态、取得时间和响应内容哈希。
4. 按上述选择器提取标题、公众号名称和正文；正文同时保存原始 HTML 与可检索纯文本，不能只保存摘要或正文开头。
5. 按 DOM 顺序收集 `#js_content img`，优先使用 `data-src`，缺失时回退 `src`；保留图片原始 URL、所在顺序、附近文字和可能的 Figure/Panel 描述。
6. 下载公开图片时保留原始字节，不做缩放、重编码或去框；使用 URL 和内容哈希去重，但不能因图片重复出现而丢失它在正文中的多个位置关系。
7. JavaScript 变量只用于补充公开发布时间、账号标识等元数据；不能依赖会话令牌、私有接口或公众号管理权限。
8. 输出 `article.json`，至少包含文章 URL、标题、公众号、发布时间、完整正文、图片记录、获取状态、取得时间和问题列表；HTML 与图片放在同一案例的本地目录。
9. 完成后检查正文首尾均存在、DOM 图片数和输出图片记录数可解释、所有失败下载均有原因。任何校验未通过时保留原始材料并进入人工回退，不静默生成不完整样本。

该结果说明单篇公开文章的 HTML 解析路线在技术上可行，但实现必须处理页面变化、访问失败、图片防盗链、重复图片、懒加载地址和个别文章缺失字段。若自动读取失败，应回退到用户导出的完整 HTML、PDF、Word 或长截图，不得绕过站点安全限制。

### 7.2 首篇试点文章

- URL：`https://mp.weixin.qq.com/s/9egbcGwrW3y_2mj6JJK9KQ?scene=1`
- 公众号：Figcheck
- 标题：《细胞全靠PS！这篇Nucleic Acids Research（影响因子=13.1）文章37处图片重复》
- 发布时间：2026-08-12 21:00
- 获取结果：HTTP 200，正文节点完整；原始 HTML 为 3,504,732 字节。
- 正文去空白字符数：2,051。此前“约 2,191 字”属于不同统计口径，不能作为精确验收数。
- 正文中有 47 个 `img` DOM 节点，其中 33 个具有实际 `data-src`/`src`，对应 33 个唯一图片 URL；33 张全部下载成功。
- 第 1–25 张图片与论文定位或重复声明有关，第 26–33 张属于推广或其他非案例内容。
- 原始 HTML、完整正文、33 张原始标注图片、元数据和处理日志均保存在 Git 忽略目录，没有提交到仓库。

这说明单篇公众号文章的公开 HTML 获取和 `data-src` 图片下载路线已得到本窗口独立验证。DOM 图片节点数与实际图片 URL 数必须分别记录，不能把占位节点误报为下载失败。

### 7.3 目标论文、撤稿和许可证核验

从公众号正文唯一定位到：

- 论文：*Real-time monitoring of DNA G-quadruplexes in living cells with a small-molecule fluorescent probe*
- 期刊：Nucleic Acids Research
- DOI：`10.1093/nar/gky665`
- PMID：`30085206`
- PMCID：`PMC6125622`

PubMed、Crossref 和 PMC 元数据相互一致。论文已于 2026-05-26 正式撤稿，撤稿 DOI 为 `10.1093/nar/gkag519`，PMID 为 `42187160`，PMCID 为 `PMC13202614`。官方撤稿说明涉及 Figure 5A、5B、5D、7A、S19、S25，并记录作者承认在 Figure 7 中复制、粘贴细胞以填满空白显微视野。正式撤稿记录可作为公众号标签的独立佐证，但不能自动替代逐区域标注。

论文及主文 Figure 标记为 CC BY-NC 4.0。根据第 6 节规则，本案例不得自动准入可发布数据集，只能先标记为 `license_review_required`，用于内部非商业可行性验证；训练、权重发布、数据再分发等场景仍需单独评审。

已从 PMC 正式文章页面取得 Figure 1–7 的干净 JPEG，并记录来源 URL、尺寸、字节数和 SHA-256。没有把公众号彩框截图作为算法输入。

### 7.4 37 处声明的解析层级

公众号文字中的 37 处可以完整闭合：

| 聚合声明 | 数量 | 干净资产状态 |
| --- | ---: | --- |
| Figure 7A 图内复制 | 4 | Figure 7 已取得 |
| Figure 7A 的 21 张图之间重复 | 21 | Figure 7 已取得 |
| Figure 5 图内复制 | 4 | Figure 5 已取得 |
| Figure S19 图内复制 | 3 | 缺 Supplement |
| Figure 5D 图间重复 | 1 | Figure 5 已取得 |
| Figure 6B 与 Figure S19 | 2 | 缺 Figure S19 |
| Figure 7A 与 Figure 6C | 1 | Figure 6/7 已取得 |
| Figure 7A 与 Figure S25 | 1 | 缺 Figure S25 |
| 合计 | 37 | 31 处只涉及已取得主文 Figure，6 处依赖 Supplement |

必须区分以下三个完成度，不能把它们混称为“映射完成”：

- 聚合文字声明解析：37/37（100%）。
- 当前首轮范围双方均已有干净主文 Figure：31 处。
- 当前 31 处范围内完成精确双区域坐标复核：0/31。

2026-08-28 用户确认首轮试点只使用正式全文中的主文 Figure 1–7。涉及 Supplement S19/S25 的 6 处标为 `deferred_supplement`，暂不进入当前样本范围，也不阻塞其余工作。当前范围因此是 31 处，保守状态为正例 0、默认负例 0、`unknown` 31，另有范围外延后项 6。`source_group` 固定为 `doi:10.1093/nar/gky665`，split 仍为 `unassigned`。

现有 `docs/IMAGE_PAIR_EVALUATION.md` schema 只能运行明确的 `positive`/`negative`，同图比较还要求两个不同区域；它能携带 `label_source`，但目前不能表达聚合声明或 `unknown`，运行结果也不保留可行性流程要求的 `review_status`。精确映射前不得用整张 Figure 或公众号彩框截图强行构造 pair。后续应先提出候选清单 schema，使聚合声明和 `unknown` 能留在预处理层；只有人工审核通过的逐对 `positive`/`negative` 子集才进入现有评测器。

### 7.5 Supplement 获取与安全阻塞处理流程（后续可选）

本案例的 Supplement 是论文正文之外的补充材料 ZIP，PMC 页面标称文件名为 `gky665_supplemental_files.zip`、大小约 4.8 MB，其中需要 Figure S19 和 Figure S25。首轮已决定暂缓 Supplement，以下流程保留给后续扩展案例范围时使用：

1. 只从 PMC、出版社正式页面、JATS 中的补充材料链接或其他许可证明确的官方来源定位附件，记录原始 URL、最终 URL、页面版本和取得时间。
2. 首先使用普通公开 HTTPS 请求；保留原始响应字节，不因文件名以 `.zip` 结尾就假定响应一定是 ZIP。
3. 校验 HTTP 状态、`Content-Type`、文件大小和魔数，并使用 Python 标准库 `zipfile.is_zipfile()` 检查格式。HTML 验证页、错误页或登录页必须记录为失败，不能改名后当作 ZIP。
4. 若官方端点要求浏览器检查，可通过正常、受支持的浏览器打开论文页面并等待自动跳转。遇到验证码时必须交给用户确认或接管，不能自动破解；遇到 HTTPS 安全警告、付费墙或其他安全拦截时不得绕过。
5. 浏览器报告下载完成后，必须再次确认文件确实落盘，记录实际大小和 SHA-256，运行 `ZipFile.testzip()`，列出归档成员，并只解压到 Git 忽略的案例目录。归档中的可执行文件不运行。
6. 若浏览器明确显示“已被您的组织屏蔽”或同类提示，应标记 `blocked_by_enterprise_dlp_policy` 并停止。可以只读检查设备是否受 MDM 管理、浏览器是否存在强制安全扩展和相关下载策略，用于解释原因；不得关闭扩展、修改策略、切换通道或降低系统安全设置来绕过。
7. 企业策略阻塞的合规回退是向单位 IT/安全管理员申请白名单或审批，或通过单位认可的渠道取得同一官方附件。收到用户提供的文件后仍需重新执行来源、格式、哈希和归档内容校验。
8. PMC AWS Article Datasets、OAI 或出版社元数据可用于交叉核验；若当前对象清单只有 XML/JSON/TXT，没有媒体或附件，这只能记录为该分发通道不包含 Supplement，不能推断论文没有补充材料。

本次实际结果是：直接 HTTP 请求得到浏览器验证 HTML，不是有效 ZIP；应用内浏览器和 Chrome 均尝试了官方页面的正常下载流程，Chrome 最终显示该 ZIP“已被您的组织屏蔽”。只读检查确认当前设备受企业管理且存在文件拦截能力，没有尝试关闭安全策略或绕过。Figure S19/S25 因此仍缺失，但已标为后续范围，不再阻塞主文 Figure 试点。

建议为附件获取至少记录：

```json
{
  "supplement_status": "blocked_by_enterprise_dlp_policy",
  "expected_filename": "gky665_supplemental_files.zip",
  "expected_size": "4.8 MB",
  "candidate_is_valid_zip": false,
  "actual_content": "browser_verification_html",
  "clean_figures_missing": ["S19", "S25"],
  "bypass_attempted": false
}
```

不要在受 Git 跟踪的文档或清单中记录单位名称、MDM 服务地址、扩展 ID、内部端口、账号或其他组织安全配置；详细诊断仅保留在本机临时记录中。

## 8. 新窗口的首要任务

1. 按仓库规定依次阅读 `AGENTS.md`、`MEMORY.md`、`docs/STATUS.md`、`docs/PRD.md` 和本文档。
2. 检查 Git 状态；本方向必须在独立开发分支处理，不得混入其他功能分支。
3. 保留现有 Git 忽略案例目录，不重新抓取已经完整取得的公众号正文、33 张图片或主文 Figure 1–7。
4. 以 Figure 5、6、7 为重点，逐条解读公众号标记框、颜色和连线，将当前范围内的 31 处声明迁移到干净主文 Figure 的两个明确 panel/区域；每条记录人工复核状态和判断理由。
5. 涉及 Figure S19/S25 的 6 处保留来源证据并标记 `deferred_supplement`，当前不下载、不映射、不计分。
6. 只有完成精确双区域复核的声明才能从 `unknown` 转为 `positive`。当前 31 处正例稳定后，再在同一主文 Figure 闭世界范围内枚举未报告 pair 作为默认负例。
7. 先提出能够表达聚合声明、`unknown`、`deferred_supplement`、`label_source` 和 `review_status` 的候选清单 schema；现有逐对评测器只读取审核完成的计分子集。
8. 首篇主文范围完整跑通并复核后，再处理 4–9 篇代表性文章验证稳定性；达到 5–10 篇后才决定是否实现批处理命令行工具，不直接开发 GUI。
9. 只有后续明确扩大范围时，才按第 7.5 节通过单位认可的渠道获取 Supplement；不得绕过企业下载策略。

## 9. 首篇试点完成标准与当前状态

| 验收项 | 当前状态 |
| --- | --- |
| 公众号正文和图片可复核 | 已完成；正文完整，47 个图片节点中 33 个实际图片 URL，33 张下载成功 |
| 唯一论文身份 | 已完成；DOI/PMID/PMCID 相互核对 |
| 撤稿或更正核验 | 已完成；正式撤稿记录可独立佐证部分 Figure |
| 公众号声明数量解析 | 聚合口径 37/37；当前主文范围 31 处，精确 pair/区域 0/31；Supplement 相关 6 处延后 |
| 干净主文 Figure | Figure 1–7 已取得并记录哈希 |
| 干净 Supplement Figure | 当前范围外；S19/S25 延后，不阻塞主文试点 |
| 许可证 | CC BY-NC 4.0，必须单独评审，不能自动准入 |
| 正例/默认负例/unknown | 当前范围为 0/0/31；另有 `deferred_supplement` 6 处 |
| `source_group` 隔离 | 已固定为论文 DOI；split 尚未分配 |
| 彩框污染控制 | 已完成来源分离；公众号截图只作证据，不作模型输入 |
| 现有评测 schema | 只适合审核后的明确 pair，不能完整表达预处理阶段 |
| Git 数据隔离 | 已完成；真实正文、图片、附件响应、清单和报告仅在 Git 忽略目录 |

## 10. 尚未完成

- 尚未把当前范围内的 31 处主文声明转换为无框干净原图上的明确图片对和双区域坐标。
- 尚未产生审核通过的正例、默认负例或可运行评测清单，也没有算法指标。
- 尚未确定候选清单 schema 的正式变更方案，现有运行器仍只接受 `positive`/`negative`。
- 尚未用 5–10 篇代表性文章验证页面变化、许可证分支和标注迁移成本，因此不应开始批量抓取或 GUI 开发。
- Supplement 及其 6 处声明已明确延后，不属于当前主文 Figure 试点的完成条件。
- 尚未决定未来使用规则算法调优、监督学习模型或两者结合。
- 尚未更新 PRD，也未授权将该能力纳入第一版产品。
