# `wiki/` 目录说明

- **与 `../raw/markdown` 目录树镜像**：若存在 `raw/markdown/主题/某篇.md`，则在本目录下维护 `wiki/主题/某篇.md`（相对路径与主文件名一致，均为 `.md`）。
- **`_synthesis/`**：多篇文献共用的主题综述、对比表、路线图；文内须引用 `raw/` 与相关单篇 `wiki/**/*.md`。
- **`_templates/paper.md`**：单篇文献 wiki 的推荐分区标题，新建文件时可复制其结构。

请勿在 `wiki/` 根下随意散落与 `raw/markdown` 不同构的冗长文件名；跨篇内容优先进 `_synthesis/`。
