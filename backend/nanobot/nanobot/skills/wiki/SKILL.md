---
name: llm-wiki
description: "LLM Wiki（文献阅读分析版）- 用 LLM 持续维护论文知识库。当用户需要导入论文/PDF、做文献综述、方法对比、实验结论追踪、维护知识库一致性时触发。关键词：文献、论文、PDF、综述、相关工作、方法对比、实验设置、SOTA。"
version: 2.0.0
---

# LLM Wiki - 文献阅读分析场景

## 核心理念

不同于“每次提问都从零检索”的 RAG，LLM Wiki 让 LLM **持续维护文献知识图谱化的 Markdown Wiki**。  
每次导入新论文，LLM 都会抽取：研究问题、方法、实验设置、指标、结论、局限性，并整合到已有主题页与对比页中。  
结论会随着新证据持续修订，形成可追溯、可迭代的研究资产。

## 三层架构

### 1. Raw Sources（原始资料）
- 存放在 `raw/` 目录
- 不可变——LLM 只读不改
- 仅保留两类：`raw/pdf/` 与 `raw/markdown/`
- 这是事实基准，可用于重建整个 wiki

### 2. Wiki（知识库）
- 存放在 `wiki/` 目录
- LLM 生成并维护的 Markdown 文件集
- 包含：`entities/`、`concepts/`、`themes/`、`tables/`、`overviews/`
- LLM 负责创建、更新、交叉引用、冲突标注与证据追踪

### 3. Schema（配置）
- 使用 `AGENTS.md` 作为唯一规则入口
- 定义结构、命名、引用规范与 Ingest/Query/Lint 工作流
- 通过持续迭代 schema，将 LLM 从聊天机器人约束为“文献管家”

## 关键目录（文献版）

- **`raw/pdf/`**：论文 PDF 原件
- **`raw/markdown/`**：读书笔记、网页剪藏、手工摘要
- **`wiki/entities/`**：论文、数据集、机构、作者等实体页
- **`wiki/concepts/`**：方法论、思想、框架页
- **`wiki/themes/`**：研究主题总结页（如 long-context、agent-eval）
- **`wiki/tables/`**：方法/论文/指标对比页
- **`wiki/overviews/`**：阶段性综述与路线图页

## 三大操作

### Ingest（导入）

当用户说“导入论文”“处理这篇 PDF”“加入文献库”时执行：

1. 读取 `raw/` 中的新资料
2. 提取文献结构化信息：
   - 研究问题 / 假设
   - 核心方法与创新点
   - 实验设置（数据集、baseline、指标）
   - 关键结果（含数值）
   - 局限性与开放问题
3. 在 `wiki/entities/` 创建或更新论文实体页
4. 在 `wiki/concepts/` 更新相关方法概念页
5. 在 `wiki/themes/` 与 `wiki/tables/` 更新主题结论与对比
6. 必要时刷新 `wiki/overviews/` 的研究脉络
7. 为每个关键结论补充来源路径（`../raw/pdf/...` 或 `../raw/markdown/...`）
8. 标注与既有文献的冲突/补充关系（不要静默覆盖）

### Query（查询）

当用户提问“某方向进展如何”“A 方法和 B 方法谁更好”“结论证据是什么”时执行：

1. 扫描 `wiki/entities|concepts|themes|tables|overviews` 定位相关页面
2. 优先读取 `themes` + `tables` 获取结构化结论，再读 `entities/concepts`
3. 生成回答并附证据引用（页面路径 + raw 来源路径）
4. 对高价值结论可回写为新主题页/概述页，沉淀为长期资产

输出优先采用：综述段落 + 对比表 + 引用清单三段式。

### Lint（维护）

当用户说“检查文献库质量”“做一次知识维护”时执行：

1. 检查页面间的矛盾
2. 检查结论是否被新论文证据推翻
3. 找出无来源支撑的“孤证结论”
4. 找出未建立对比关系的方法页
5. 发现缺失的交叉引用与主题归类
6. 标注证据不足处并给出补充阅读建议

## 初始化工作流

当用户说"创建知识库"、"初始化 wiki"时：

1. 创建目录结构：
   ```
   raw/
   ├─ pdf/
   └─ markdown/
   wiki/
   ├─ entities/
   ├─ concepts/
   ├─ themes/
   ├─ tables/
   └─ overviews/
   ```
2. 创建 `AGENTS.md`（schema 规则）
3. 创建各目录 `_template.md`（页面模板）

## 最佳实践

- 一次导入一篇核心论文，人工确认关键结论后再批量导入
- 所有“方法优劣”结论必须绑定实验设置和指标，防止脱离上下文
- 定期维护 `themes` 和 `tables`，这是回答问题的主入口
- 查询高价值答案应回写 wiki，避免洞见只留在聊天记录
- 使用 Git 保留版本历史，便于追踪观点演化

---

## 文献页面 Frontmatter 建议

```yaml
---
tags: [paper, llm, long-context]
type: entity
title: "Paper Title"
year: 2026
venue: "ICLR"
source_path: "../raw/pdf/2026-04-21__iclr__paper-title.pdf"
---
```
