# Delegation catalog (reference only)

Use the lists below to choose `skills` and `tools` for each `spawn`. You cannot call executor tools on the main path.

**Stage rule:** one stage = one skill/tool **profile** and one deliverable. Do not create separate stages for headings / RAG / read / summarize — that entire chain belongs in **one** literature spawn.

### Common spawn profiles

| Stage kind | `tools` | `skills` | One spawn should… |
|------------|---------|----------|-------------------|
| Literature / methods | `rag_search`, `get_markdown_headings`, `read_file` | `markdown`, `zotero` | Return method steps, formulas, section refs (subagent picks tools internally) |
| Write docx (create + verify) | `mcp_docx-mcp_create_from_markdown`, `mcp_docx-mcp_open_document`, `mcp_docx-mcp_get_document_info`, `mcp_docx-mcp_get_body_text`, `mcp_docx-mcp_get_headings`, `mcp_docx-mcp_search_text` | `docx` | Create under `docx/` from prior stage notes; verify with **`get_body_text` first** |
| Revise docx | `mcp_docx-mcp_open_document`, `mcp_docx-mcp_get_body_text`, `mcp_docx-mcp_save_document`, … edit tools … | `docx` | Read with `get_body_text`; edit user-specified path in place |

### Example: “总结抗干扰步骤并写入 docx”

When you judge orchestration is needed, **`plan_tasks` once** then **`spawn`** per stage:

```text
plan_tasks(stages=["从文献 markdown 提取抗干扰方法步骤与公式要点", "写入 docx/报告.docx 并 get_body_text 验证"])
spawn(stage 1, literature profile, context=markdown path + user goal)
spawn(stage 2, docx profile, context=prior stage results + output path)
```

Two stages, two spawns — not five.

## Skills

{{ skills_summary }}

{{ tools_section }}
