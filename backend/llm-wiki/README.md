# LLM Wiki（文献阅读分析版）

## 简介

这个版本专门面向“论文阅读 -> 主题分析 -> 结论追踪”。  
目标是让 LLM 持续维护一个可追溯的文献 wiki，而不是每次问题都临时检索。
边界：LLM 不负责将 PDF/Markdown 文件放入 `raw/`，仅处理已由本地 Zotero 同步好的资料。

## 目录结构

```text
backend/llm-wiki/
  raw/                    # 原始资料层（不可变，与本地 Zotero 目录一致）
    pdf/                  # 论文 PDF（与本地 Zotero 对应目录一致）
    markdown/             # 笔记/剪藏/提取文本（与本地 Zotero 对应目录一致）
  wiki/                   # LLM 维护层
    entities/             # 论文、作者、机构、数据集
    concepts/             # 方法与框架
    themes/               # 主题总结
    tables/               # 对比表（方法、指标、设置）
    overviews/            # 综合概述
  AGENTS.md               # 唯一 schema 规则入口
  llm-wiki.md             # 模式说明（本仓库版）
```

## 核心流程

### 1) Ingest（导入文献）

当 `raw/pdf/*.pdf` 或 `raw/markdown/*.md` 已由本地 Zotero 同步完成后（两者应与本地 Zotero 目录保持一致），LLM 应：

1. 读取原始资料（只读，不改）
2. 提取研究问题、方法、实验设置、关键结果、局限性
3. 更新 `wiki/entities|concepts|themes|tables|overviews`
4. 为关键结论写上来源路径（`../raw/pdf/...` 或 `../raw/markdown/...`）

### 2) Query（问答分析）

当你提问“这个方向谁更好/证据是什么/还有哪些空白”时，LLM 应：

1. 扫描 `wiki` 五类目录
2. 优先读 `themes` 和 `tables` 获取结构化结论
3. 回答时附上证据链接
4. 将高价值答案沉淀回 wiki 页面

### 3) Lint（知识维护）

定期执行：

- 过时结论检查（是否被新文献推翻）
- 缺失引用检查（是否可回溯 raw）
- 冲突检查（是否有互斥结论未标注）
- 结构检查（孤立页面、缺失对比）

## 页面建议（最小模板）

- 实体页：摘要、关键结果、证据与来源、相关页面
- 概念页：定义、核心机制、适用条件、证据与来源
- 主题页：阶段性结论、争议点、下一步问题
- 对比页：统一设置下的指标对比
- 概述页：领域全景 + 路线图

## 使用建议

- 每次优先导入“高信息密度”论文（survey、benchmark、SOTA）
- 方法结论必须绑定实验设置，避免脱离上下文
- 每周做一次 lint，保持 wiki 可用性
- `raw/pdf` 与 `raw/markdown` 作为本地 Zotero 资料的同步镜像目录，目录结构与文件命名应保持一致
- 文件搬运与同步由外部流程负责（如 Zotero 同步脚本）；LLM 仅消费 `raw/` 中已存在的文件

