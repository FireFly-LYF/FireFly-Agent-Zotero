---
name: skill-creator
description: 创建或更新 Agent Skills；用于设计、结构化和打包技能。
---

# 技能创造者

该技能为创建有效技能提供了指导。

## 关于技能

技能是模块化的、独立的包，通过提供
专业知识、工作流程和工具。将它们视为特定的“入职指南”
域或任务——它们将代理从通用代理转变为专用代理
配备了任何模型都无法完全拥有的程序知识。

### 提供什么技能

1. 专业工作流程 - 针对特定领域的多步骤程序
2. 工具集成 - 使用特定文件格式或 API 的说明
3. 领域专业知识 - 公司特定的知识、模式、业务逻辑
4. 捆绑资源 - 用于复杂和重复任务的脚本、参考和资产

## 核心原则

### 简洁是关键

上下文窗口是一种公共物品。技能与代理所需的其他所有内容共享上下文窗口：系统提示、对话历史记录、其他技能的元数据以及实际的用户请求。

**默认假设：代理已经非常聪明。 ** 仅添加代理尚未拥有的上下文。挑战每一条信息：“特工真的需要这个解释吗？”和“这一段是否证明其代币成本合理？”

比起冗长的解释，更喜欢简洁的例子。

### 设置适当的自由度

将特异性水平与任务的脆弱性和可变性相匹配：

**高自由度（基于文本的说明）**：当多种方法有效、决策取决于上下文或启发式指导方法时使用。

**中等自由度（伪代码或带参数的脚本）**：当存在首选模式、某些变化可接受或配置影响行为时使用。

**低自由度（特定脚本，很少参数）**：当操作脆弱且容易出错、一致性至关重要或必须遵循特定顺序时使用。

将智能体视为探索一条路径：一座带有悬崖的窄桥需要特定的护栏（低自由度），而开阔的场地允许许多路线（高自由度）。

### 技能剖析

每个技能都包含必需的 SKILL.md 文件和可选的捆绑资源：

```
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter metadata (required)
│   │   ├── name: (required)
│   │   └── description: (required)
│   └── Markdown instructions (required)
└── Bundled Resources (optional)
    ├── scripts/          - Executable code (Python/Bash/etc.)
    ├── references/       - Documentation intended to be loaded into context as needed
    └── assets/           - Files used in output (templates, icons, fonts, etc.)
```

#### 技能.md（必填）

每个 SKILL.md 都包含：

- **Frontmatter** (YAML)：包含 `name` 和 `description` 字段。这些是代理读取以确定何时使用该技能的唯一字段，因此清晰、全面地描述该技能是什么以及何时应该使用该技能非常重要。
- **正文**（Markdown）：使用该技能的说明和指导。仅在技能触发后加载（如果有的话）。

#### 捆绑资源（可选）

##### 脚本 (`scripts/`)

用于需要确定性可靠性或重复重写的任务的可执行代码（Python/Bash/等）。

- **何时包含**：当重复重写相同的代码或需要确定性可靠性时
- **示例**：`scripts/rotate_pdf.py` 用于 PDF 旋转任务
- **优点**：代币高效，确定性，可以在不加载到上下文的情况下执行
- **注意**：代理可能仍需要读取脚本以进行修补或特定于环境的调整

##### 参考文献 (`references/`)

旨在根据需要加载到上下文中的文档和参考材料，以告知代理的流程和思维。

- **何时包含**：代理在工作时应参考的文档
- **示例**：`references/finance.md` 表示财务模式，`references/mnda.md` 表示公司 NDA 模板，`references/policies.md` 表示公司政策，`references/api_docs.md` 表示 API 规范
- **用例**：数据库模式、API 文档、领域知识、公司政策、详细的工作流程指南
- **优点**：保持 SKILL.md 的精简，仅在代理确定需要时才加载
- **最佳实践**：如果文件很大（>10k 字），请在 SKILL.md 中包含 grep 或 glob 模式，以便代理可以有效地使用内置搜索工具；提及默认的 `grep(output_mode="files_with_matches")`、`grep(output_mode="count")`、`grep(fixed_strings=true)`、`glob(entry_type="dirs")` 或通过 `head_limit` / `offset` 分页是正确的第一步
- **避免重复**：信息应位于 SKILL.md 或参考文件中，而不是两者中。优先选择参考文件来获取详细信息，除非它确实是技能的核心，这可以使 SKILL.md 保持精简，同时使信息可在不占用上下文窗口的情况下被发现。仅在 SKILL.md 中保留必要的程序说明和工作流程指南；将详细的参考材料、架构和示例移至参考文件。

##### 资产 (`assets/`)

文件不打算加载到上下文中，而是在代理生成的输出中使用。

- **何时包含**：当技能需要将在最终输出中使用的文件时
- **示例**：`assets/logo.png` 用于品牌资产、`assets/slides.pptx` 用于 PowerPoint 模板、`assets/frontend-template/` 用于 HTML/React 样板、`assets/font.ttf` 用于排版
- **用例**：复制或修改的模板、图像、图标、样板代码、字体、示例文档
- **优点**：将输出资源与文档分开，使代理能够使用文件而无需将其加载到上下文中

#### 技能中不应该包含哪些内容

技能应该只包含直接支持其功能的基本文件。请勿创建无关的文档或辅助文件，包括：

- 自述文件.md
- 安装_指南.md
- QUICK_REFERENCE.md
- 变更日志.md
- ETC。

该技能应该只包含人工智能代理完成手头工作所需的信息。它不应包含有关创建过程、设置和测试过程、面向用户的文档等的辅助上下文。创建额外的文档文件只会增加混乱和混乱。

### 渐进式披露设计原则

技能使用三级加载系统来有效管理上下文：

1. **元数据（名称 + 描述）** - 始终处于上下文中（约 100 个单词）
2. **SKILL.md body** - 技能触发时（<5k 字）
3. **捆绑资源** - 根据代理的需要（无限制，因为脚本可以在不读入上下文窗口的情况下执行）

#### 渐进式披露模式

将 SKILL.md 正文保持在 500 行以下，以尽量减少上下文膨胀。当接近此限制时，将内容拆分为单独的文件。将内容拆分到其他文件时，从 SKILL.md 中引用它们并清楚地描述何时阅读它们非常重要，以确保技能的读者知道它们存在以及何时使用它们。

**关键原则：** 当一项技能支持多种变体、框架或选项时，仅在 SKILL.md 中保留核心工作流程和选择指南。将特定于变体的详细信息（模式、示例、配置）移至单独的参考文件中。

**模式 1：带有参考文献的高级指南**

```markdown
# PDF Processing

## Quick start

Extract text with pdfplumber:
[code example]

## Advanced features

- **Form filling**: See [FORMS.md](FORMS.md) for complete guide
- **API reference**: See [REFERENCE.md](REFERENCE.md) for all methods
- **Examples**: See [EXAMPLES.md](EXAMPLES.md) for common patterns
```

代理仅在需要时加载 FORMS.md、REFERENCE.md 或 Examples.md。

**模式 2：特定领域的组织**

对于具有多个领域的技能，请按领域组织内容以避免加载不相关的上下文：

```
bigquery-skill/
├── SKILL.md (overview and navigation)
└── reference/
    ├── finance.md (revenue, billing metrics)
    ├── sales.md (opportunities, pipeline)
    ├── product.md (API usage, features)
    └── marketing.md (campaigns, attribution)
```

当用户询问销售指标时，代理仅读取 sales.md。

同样，对于支持多个框架或变体的技能，请按变体进行组织：

```
cloud-deploy/
├── SKILL.md (workflow + provider selection)
└── references/
    ├── aws.md (AWS deployment patterns)
    ├── gcp.md (GCP deployment patterns)
    └── azure.md (Azure deployment patterns)
```

当用户选择AWS时，代理仅读取aws.md。

**模式 3：条件细节**

显示基本内容，高级内容链接：

```markdown
# DOCX Processing

## Creating documents

Use docx-js for new documents. See [DOCX-JS.md](DOCX-JS.md).

## Editing documents

For simple edits, modify the XML directly.

**For tracked changes**: See [REDLINING.md](REDLINING.md)
**For OOXML details**: See [OOXML.md](OOXML.md)
```

仅当用户需要这些功能时，代理才会读取 REDLINING.md 或 OOXML.md。

**重要指南：**

- **避免深层嵌套引用** - 将引用保留在 SKILL.md 的深一层。所有参考文件应直接从 SKILL.md 链接。
- **构建较长的参考文件** - 对于超过 100 行的文件，请在顶部包含一个目录，以便代理在预览时可以看到完整范围。

## 技能创造过程

技能创建涉及以下步骤：

1. 通过具体例子理解技巧
2. 规划可重用的技能内容（脚本、参考文献、资产）
3. 初始化技能（运行init_skill.py）
4. 编辑技能（实现资源并编写SKILL.md）
5. 打包技能（运行package_skill.py）
6. 根据实际使用情况进行迭代

按顺序执行这些步骤，仅当有明确原因不适用时才跳过。

### 技能命名

- 仅使用小写字母、数字和连字符；将用户提供的标题标准化为连字符大小写（例如，“计划模式”-> `plan-mode`）。
- 生成名称时，生成不超过 64 个字符（字母、数字、连字符）的名称。
- 更喜欢用简短的、以动词为主导的短语来描述动作。
- 按工具划分的命名空间可以提高清晰度或触发性（例如 `gh-address-comments`、`linear-address-issue`）。
- 按照技能名称准确命名技能文件夹。

### 第一步：通过具体例子理解技能

仅当已经清楚地了解技能的使用模式时才跳过此步骤。即使使用现有技能，它仍然很有价值。

要创建有效的技能，请清楚地了解如何使用该技能的具体示例。这种理解可以来自直接的用户示例，也可以来自通过用户反馈验证的生成示例。

例如，在培养图像编辑技能时，相关问题包括：

- “图像编辑器技能应该支持哪些功能？编辑、旋转还是其他功能？”
- “你能举例说明如何使用这项技能吗？”
- “我可以想象用户会要求‘消除该图像的红眼’或‘旋转该图像’之类的问题。您认为这项技能还有其他使用方式吗？”
- “用户会说什么来触发这个技能？”

为了避免让用户感到不知所措，请避免在一条消息中提出太多问题。从最重要的问题开始，并根据需要进行跟进，以提高效率。

当清楚地了解技能应支持的功能时，结束此步骤。

### 第二步：规划可重复使用的技能内容

要将具体示例转化为有效技能，请通过以下方式分析每个示例：

1. 考虑如何从头开始执行示例
2. 确定哪些脚本、引用和资产在重复执行这些工作流程时会有所帮助

示例：当构建 `pdf-editor` 技能来处理“帮我旋转此 PDF”之类的查询时，分析显示：

1. 旋转 PDF 需要每次重新编写相同的代码
2. `scripts/rotate_pdf.py` 脚本有助于存储在技能中

示例：当为“为我构建一个待办事项应用程序”或“为我构建一个仪表板来跟踪我的步骤”等查询设计 `frontend-webapp-builder` 技能时，分析显示：

1. 每次编写前端 Web 应用程序都需要相同的 HTML/React 样板
2. 包含样板 HTML/React 项目文件的 `assets/hello-world/` 模板有助于存储在技能中

示例：在构建 `big-query` 技能来处理“今天有多少用户登录？”之类的查询时分析表明：

1. 查询 BigQuery 需要每次重新发现表架构和关系
2. 记录表模式的 `references/schema.md` 文件有助于存储在技能中

要确定技能的内容，请分析每个具体示例以创建可重用资源的列表，其中包括：脚本、引用和资产。

### 第三步：初始化技能

此时，是时候实际创建该技能了。

仅当正在开发的技能已经存在并且需要迭代或打包时，才跳过此步骤。在这种情况下，请继续执行下一步。

从头开始创建新技能时，请始终运行 `init_skill.py` 脚本。该脚本可以方便地生成一个新的模板技能目录，该目录自动包含技能所需的所有内容，从而使技能创建过程更加高效和可靠。

对于 `nanobot`，自定义技能应位于活动工作区 `skills/` 目录下，以便可以在运行时自动发现它们（例如 `<workspace>/skills/my-skill/SKILL.md`）。

用法：

```bash
scripts/init_skill.py <skill-name> --path <output-directory> [--resources scripts,references,assets] [--examples]
```

示例：

```bash
scripts/init_skill.py my-skill --path ./workspace/skills
scripts/init_skill.py my-skill --path ./workspace/skills --resources scripts,references
scripts/init_skill.py my-skill --path ./workspace/skills --resources scripts --examples
```

脚本：

- 在指定路径创建技能目录
- 生成具有正确 frontmatter 和 TODO 占位符的 SKILL.md 模板
- （可选）基于 `--resources` 创建资源目录
- 设置 `--examples` 时可以选择添加示例文件

初始化完成后，自定义SKILL.md并根据需要添加资源。如果您使用 `--examples`，请替换或删除占位符文件。

### 第四步：编辑技能

编辑（新生成的或现有的）技能时，请记住该技能是为代理的另一个实例创建的以供使用。包括对代理人有利且非显而易见的信息。 Consider what procedural knowledge, domain-specific details, or reusable assets would help another agent instance execute these tasks more effectively.

#### 学习经过验证的设计模式

根据您的技能需求查阅这些有用的指南：

- **多步骤流程**：有关顺序工作流程和条件逻辑，请参阅references/workflows.md
- **特定输出格式或质量标准**：有关模板和示例模式，请参阅references/output-patterns.md

这些文件包含有效技能设计的既定最佳实践。

#### 从可重复使用的技能内容开始

要开始实施，请从上面标识的可重用资源开始：`scripts/`、`references/` 和 `assets/` 文件。请注意，此步骤可能需要用户输入。例如，在实现 `brand-guidelines` 技能时，用户可能需要提供品牌资产或模板以存储在 `assets/` 中，或提供文档以存储在 `references/` 中。

添加的脚本必须通过实际运行进行测试，以确保没有错误并且输出符合预期。如果有许多类似的脚本，则只需测试代表性示例即可确保它们都能正常工作，同时平衡完成时间。

如果您使用 `--examples`，请删除该技能不需要的任何占位符文件。只创建实际需要的资源目录。

#### 更新技能.md

**写作指南：** 始终使用命令式/不定式形式。

##### 前题

使用 `name` 和 `description` 编写 YAML frontmatter：

- `name`：技能名称
- `description`：这是技能的主要触发机制，有助于代理了解何时使用该技能。
  - 包括该技能的用途以及何时使用该技能的特定触发器/上下文。
  - 此处包含所有“何时使用”信息 - 不在正文中。主体仅在触发后才加载，因此主体中的“何时使用此技能”部分对代理没有帮助。
  - `docx` 技能的示例描述：“全面的文档创建、编辑和分析，支持跟踪更改、注释、格式保存和文本提取。当客服人员需要处理专业文档（.docx 文件）以执行以下操作时使用：(1) 创建新文档，(2) 修改或编辑内容，(3) 使用跟踪更改，(4) 添加注释或任何其他文档任务”

尽量减少前面的内容。在 `nanobot` 中，需要时也支持 `metadata` 和 `always`，但除非实际需要，否则请避免添加额外的字段。

##### 身体

编写使用该技能及其捆绑资源的说明。

### 第 5 步：打包技能

技能开发完成后，必须将其打包到可分发的 .skill 文件中以与用户共享。打包过程首先自动验证技能，以确保其满足所有要求：

```bash
scripts/package_skill.py <path/to/skill-folder>
```

可选输出目录规范：

```bash
scripts/package_skill.py <path/to/skill-folder> ./dist
```

打包脚本将：

1. **自动验证**技能，检查：
   - YAML frontmatter 格式和必填字段
   - 技能命名约定和目录结构
   - 描述的完整性和质量
   - 文件组织和资源参考

2. 如果验证通过，则打包**技能，创建一个以技能命名的 .skill 文件（例如 `my-skill.skill`），其中包含所有文件并维护用于分发的正确目录结构。 .skill 文件是扩展名为 .skill 的 zip 文件。

   安全限制：当存在任何符号链接时，符号链接将被拒绝并且打包失败。

如果验证失败，脚本将报告错误并退出而不创建包。修复所有验证错误并再次运行打包命令。

### 第 6 步：迭代

测试技能后，用户可以请求改进。通常，这种情况会在使用该技能后立即发生，并提供该技能如何执行的新背景。

**迭代工作流程：**

1. 在实际任务中使用该技能
2. 注意困难或低效率
3. 确定如何更新 SKILL.md 或捆绑资源
4. 实施更改并再次测试
