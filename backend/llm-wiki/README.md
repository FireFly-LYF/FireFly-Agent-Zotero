# LLM Wiki（文献阅读分析版）

## 简介

这个版本专门面向「论文阅读 → 主题分析 → 结论追踪」。  
目标是让 LLM 持续维护一个可追溯的文献 wiki，而不是每次问题都临时检索。  
边界：LLM 不负责将 PDF/Markdown 文件放入 `raw/`，仅处理已由本地 Zotero 同步好的资料。

## 目录结构

```text
backend/llm-wiki/
  raw/                    # 原始资料层（不可变，与本地 Zotero 目录一致）
    pdf/                  # 论文 PDF（目录镜像）
    markdown/             # 笔记/剪藏/提取文本（目录镜像）
    rag/                  # 检索用切片索引（由脚本生成）
  wiki/                   # LLM 维护层
    <与 raw/markdown 相同的子目录树>/
      <与 markdown 同主名>.md   # 每篇文献唯一 wiki 入口（内部分区见 _templates/paper.md）
    _synthesis/           # 跨多篇：主题综述、对比表、路线图等（可选）
    _templates/         # 单篇文件分区模板（只读参考，可复制）
  AGENTS.md               # 唯一 schema 规则入口
  llm-wiki.md             # 模式说明（本仓库版）
```

## 核心流程

### 1) Ingest（导入文献）

当 **`raw/markdown/*.md`** 已存在（通常先经 `raw/pdf` 同步再解析为 markdown）后，LLM 应：

1. 读取原始资料（只读，不改）
2. 提取研究问题、方法、实验设置、关键结果、局限性
3. 在 **`wiki/` 中与该 `raw/markdown` 文件同相对路径、同主名** 处创建或更新 **一个** `.md` 文件，并按模板填写各分区
4. 若涉及多篇对比或领域总览，更新 **`wiki/_synthesis/`** 下相应文件
5. 「来源」写上 **`raw/markdown/...`**（与本页镜像路径一致）；尚无 `.md` 时可在正文备注待解析 PDF，生成后改成 markdown 路径

### 2) Query（问答分析）

当你提问「这个方向谁更好 / 证据是什么 / 还有哪些空白」时，LLM 应：

1. 先查看 **`wiki/_synthesis/`** 是否已有相关综述或对比表
2. 再阅读相关单篇 **`wiki/**/*.md`**
3. 回答时附上证据链接（`raw` + `wiki`）
4. 将高价值答案沉淀回对应单篇文件或 `_synthesis`

### 3) Lint（知识维护）

定期执行：

- 过时结论检查（是否被新文献推翻）
- 缺失引用检查（是否可回溯 raw）
- 冲突检查（是否有互斥结论未标注）
- **`raw/markdown` 与 `wiki` 镜像是否成对**、`_synthesis` 是否缺少依据引用

## 单篇文件内写什么（概念上对应旧五类）

单篇 `.md` 内用二级标题区分（详见 `wiki/_templates/paper.md`）：

- 文献实体与元信息（原 entities）
- 概念与方法（原 concepts）
- 主题要点（仅本篇可支撑的结论；领域级综述放 `_synthesis`）
- 对比与数据（以本篇为主；多文献对比表放 `_synthesis`）
- 概述与导航（本篇在更大主题中的位置）
- 来源（必填：`raw/markdown/...` 相对路径；与当前 `wiki/...` 页除根目录外路径一致）

## 使用建议

- 每次优先导入「高信息密度」论文（survey、benchmark、SOTA）
- 方法结论必须绑定实验设置，避免脱离上下文
- 每周做一次 lint，保持 wiki 可用性
- `raw/pdf` 与 `raw/markdown` 作为本地 Zotero 资料的同步镜像目录，目录结构与文件命名应保持一致
- 文件搬运与同步由外部流程负责（如 Zotero 同步脚本）；LLM 仅消费 `raw/` 中已存在的文件
