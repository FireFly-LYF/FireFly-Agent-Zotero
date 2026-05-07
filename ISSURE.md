# 冒号与行内公式识别（插件 · `renderMarkdownLite`）

## 现象

在助手气泡中，**列表项**里若同时包含：

- 半角/全角**冒号**（`:` / `：`），且  
- 行内公式 `$...$`，

有时会出现公式**仍以字面量 `$...$` 显示**，而块级公式（独立成行、灰底）往往正常。

## 原因

列表行在检测到冒号时会走 `enhanceFormulaSegmentAfterColon`（`plugin/src/modules/itemPaneLLMUI.ts`）。早期实现用正则要求：

- 从行首到**第一个**冒号之间，非冒号字符**至多 80 个**（`[^:：]{0,80}[:：]`）。

当正文较长、第一个「：」出现在「表示为：」「如下：」等**靠后**位置时，**整段无法匹配**。逻辑会退回 `escapeHtml(src)`，**不再调用** `renderInlineMarkdownLite`，因此所有 `$...$` 都不会交给 KaTeX，表现为公式「无法识别」。

简言之：**不是冒号本身破坏 LaTeX，而是「冒号前字数上限」导致整段未进入公式解析分支。**

## 修复思路（已实现）

- 按**第一个**半角/全角冒号拆分前缀与后缀，**去掉「冒号前至多 N 字」的限制**；  
- 若该行**没有**冒号，则直接走 `renderInlineMarkdownLite`。

这样在长句 + 后置「：」的场景下，行内公式仍可正常渲染。

## 相关代码位置

- `plugin/src/modules/itemPaneLLMUI.ts`：`enhanceFormulaSegmentAfterColon`、`renderMarkdownLite` 中列表项分支。
