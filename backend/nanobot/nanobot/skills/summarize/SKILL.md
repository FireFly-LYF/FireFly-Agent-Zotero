---
name: summarize
description: 总结 URL、本地文件与 YouTube 内容；也可用于提取转录文本。
homepage: https://summarize.sh
metadata: {"nanobot":{"emoji":"🧾","requires":{"bins":["summarize"]},"install":[{"id":"brew","kind":"brew","formula":"steipete/tap/summarize","bins":["summarize"],"label":"安装 summarize (brew)"}]}}
---

# 总结

用于汇总 URL、本地文件和 YouTube 链接的快速 CLI。

## 何时使用（触发短语）

当用户提出以下任何问题时立即使用此技能：
- “使用summary.sh”
- “这个链接/视频是关于什么的？”
- “总结一下这个网址/文章”
- “转录此 YouTube/视频”（尽力提取转录内容；无需 `yt-dlp`）

## 快速启动

```bash
summarize "https://example.com" --model google/gemini-3-flash-preview
summarize "/path/to/file.pdf" --model google/gemini-3-flash-preview
summarize "https://youtu.be/dQw4w9WgXcQ" --youtube auto
```

## YouTube：摘要与文字记录

尽力而为的成绩单（仅限 URL）：

```bash
summarize "https://youtu.be/dQw4w9WgXcQ" --youtube auto --extract-only
```

如果用户要求提供文字记录，但文字内容很大，请先返回一个简短的摘要，然后询问要扩展哪个部分/时间范围。

## 型号+钥匙

为您选择的提供商设置 API 密钥：
- 开放人工智能：`OPENAI_API_KEY`
- 人择：`ANTHROPIC_API_KEY`
- xAI：`XAI_API_KEY`
- Google：`GEMINI_API_KEY`（别名：`GOOGLE_GENERATIVE_AI_API_KEY`、`GOOGLE_API_KEY`）

如果未设置，则默认模型为 `google/gemini-3-flash-preview`。

## 有用的标志

- `--model <provider/model>`（指定模型）
- `--youtube auto`（启用 YouTube 自动策略）
- `--extract-only`（仅限 URL）
- `--json`（机器可读）
- `--firecrawl auto|off|always`（后备提取）
- `--youtube auto`（如果设置了 `APIFY_API_TOKEN`，则 Apify 回退）

## 配置

可选配置文件：`~/.summarize/config.json`

```json
{ "model": "openai/gpt-5.2" }
```

可选服务：
- `FIRECRAWL_API_KEY` 用于被阻止的网站
- `APIFY_API_TOKEN` 用于 YouTube 后备
