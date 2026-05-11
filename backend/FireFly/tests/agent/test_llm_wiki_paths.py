import shutil
import tempfile
from pathlib import Path

from firefly.skills.wiki.scripts.llm_wiki_paths import (
    load_llm_wiki_agents_md,
    resolve_llm_wiki_root,
    resolve_wiki_mirror_from_raw_pdf_path,
)


def test_resolve_llm_wiki_root_finds_sibling() -> None:
    base = Path(tempfile.mkdtemp(prefix="ff_llm_wiki_"))
    try:
        wiki = base / "llm-wiki" / "wiki"
        wiki.mkdir(parents=True)
        ws = base / "workspace"
        ws.mkdir()
        assert resolve_llm_wiki_root(ws) == (base / "llm-wiki").resolve()
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_resolve_llm_wiki_root_none_when_missing() -> None:
    base = Path(tempfile.mkdtemp(prefix="ff_llm_wiki_"))
    try:
        ws = base / "workspace"
        ws.mkdir()
        assert resolve_llm_wiki_root(ws) is None
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_resolve_wiki_mirror_from_raw_pdf_path() -> None:
    pdf_str = "/repo/backend/llm-wiki/raw/pdf/强化学习/频率捷变/paper.pdf"
    out = resolve_wiki_mirror_from_raw_pdf_path(pdf_str)
    assert out is not None
    wr, wp = out
    assert wr == Path("/repo/backend/llm-wiki")
    assert wp == Path("/repo/backend/llm-wiki/wiki/强化学习/频率捷变/paper.md")


def test_resolve_wiki_mirror_from_raw_pdf_path_none() -> None:
    assert resolve_wiki_mirror_from_raw_pdf_path("/tmp/foo.pdf") is None


def test_load_llm_wiki_agents_md() -> None:
    base = Path(tempfile.mkdtemp(prefix="ff_llm_wiki_"))
    try:
        root = base / "llm-wiki"
        (root / "wiki").mkdir(parents=True)
        (root / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
        assert load_llm_wiki_agents_md(root) == "# Rules"
        assert load_llm_wiki_agents_md(base / "nope") is None
    finally:
        shutil.rmtree(base, ignore_errors=True)
