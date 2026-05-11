from pathlib import Path

from firefly.skills.wiki.scripts.wiki_markdown_ingest import wiki_output_path_for_raw_markdown


def test_wiki_output_path_mirrors_raw_markdown() -> None:
    root = Path("llm-wiki")
    md = root / "raw" / "markdown" / "抗干扰" / "子" / "篇.md"
    out = wiki_output_path_for_raw_markdown(root, md)
    assert out == root / "wiki" / "抗干扰" / "子" / "篇.md"
