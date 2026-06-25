#!/usr/bin/env python3
"""Read Zotero library metadata, notes, annotations, and optional attachment text."""

from __future__ import annotations

import argparse
import configparser
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any

_ANNOTATION_TYPES = {
    0: "highlight",
    1: "note",
    2: "image",
    3: "ink",
}

_BIB_ITEM_TYPES = frozenset(
    {
        "artwork",
        "audioRecording",
        "bill",
        "blogPost",
        "book",
        "bookSection",
        "case",
        "computerProgram",
        "conferencePaper",
        "dataset",
        "dictionaryEntry",
        "document",
        "email",
        "encyclopediaArticle",
        "film",
        "forumPost",
        "hearing",
        "instantMessage",
        "interview",
        "journalArticle",
        "letter",
        "magazineArticle",
        "manuscript",
        "map",
        "newspaperArticle",
        "patent",
        "podcast",
        "preprint",
        "presentation",
        "radioBroadcast",
        "report",
        "standard",
        "statute",
        "thesis",
        "tvBroadcast",
        "videoRecording",
        "webpage",
    }
)

_MAX_STORAGE_TEXT_CHARS = 32_000


class ZoteroReaderError(Exception):
    """Raised when the Zotero database cannot be read."""


def _strip_html(text: str) -> str:
    if not text:
        return ""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _ro_connect(db_path: Path) -> sqlite3.Connection:
    uri = db_path.expanduser().resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@dataclass(frozen=True, slots=True)
class _ZoteroSchema:
    """Observed Zotero SQLite layout (varies by client version / test fixtures)."""

    items_has_parent_item_id: bool
    has_item_notes: bool


def _detect_schema(conn: sqlite3.Connection) -> _ZoteroSchema:
    """Detect how parent-child links and note bodies are stored.

    Official Zotero (5+) keeps ``items`` flat: parent links live in
    ``itemNotes.parentItemID`` and ``itemAttachments.parentItemID``, not on
    ``items``. Early test fixtures incorrectly put ``parentItemID`` on ``items``.
    """
    item_cols = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(items)")
    }
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    return _ZoteroSchema(
        items_has_parent_item_id="parentItemID" in item_cols,
        has_item_notes="itemNotes" in tables,
    )


def _top_level_bib_filter(schema: _ZoteroSchema) -> str:
    """SQL fragment excluding child note/attachment rows when needed."""
    if schema.items_has_parent_item_id:
        return "i.parentItemID IS NULL AND "
    # Real Zotero: notes/attachments are separate item types; _BIB_ITEM_TYPES
    # already excludes them, so no extra parent column filter is required.
    return ""


def _parse_prefs_data_dir(prefs_path: Path) -> Path | None:
    if not prefs_path.is_file():
        return None
    try:
        text = prefs_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = re.search(
        r'user_pref\("extensions\.zotero\.dataDir"\s*,\s*"((?:\\.|[^"\\])*)"\)',
        text,
    )
    if not m:
        return None
    raw = m.group(1).encode("utf-8").decode("unicode_escape")
    return Path(raw).expanduser()


def _profile_dirs_from_ini(ini_path: Path) -> list[Path]:
    if not ini_path.is_file():
        return []
    parser = configparser.ConfigParser()
    try:
        parser.read(ini_path, encoding="utf-8")
    except configparser.Error:
        return []
    root = ini_path.parent
    profiles: list[Path] = []
    for section in parser.sections():
        if not section.startswith("Install"):
            continue
        rel = parser.get(section, "Default", fallback="").strip()
        if rel:
            profiles.append((root / rel).resolve())
    return profiles


def _candidate_data_dirs() -> list[Path]:
    dirs: list[Path] = []
    env = os.environ.get("ZOTERO_DATA_DIR", "").strip()
    if env:
        dirs.append(Path(env).expanduser())

    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            zotero_root = Path(appdata) / "Zotero" / "Zotero"
            dirs.extend(_profile_dirs_from_ini(zotero_root / "profiles.ini"))
            dirs.append(zotero_root)
    else:
        home = Path.home()
        zotero_root = home / ".zotero" / "zotero"
        dirs.extend(_profile_dirs_from_ini(zotero_root / "profiles.ini"))
        dirs.append(zotero_root)

    dirs.append(Path.home() / "Zotero")

    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        key = str(d.expanduser().resolve()) if d.exists() else str(d)
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def resolve_zotero_data_dir(data_dir: str | Path | None = None) -> Path:
    """Locate the Zotero data directory containing ``zotero.sqlite``."""
    if data_dir:
        root = Path(data_dir).expanduser().resolve()
        if (root / "zotero.sqlite").is_file():
            return root
        raise ZoteroReaderError(f"zotero.sqlite not found under {root}")

    for base in _candidate_data_dirs():
        prefs_data = _parse_prefs_data_dir(base / "prefs.js")
        if prefs_data and (prefs_data / "zotero.sqlite").is_file():
            return prefs_data.resolve()
        if (base / "zotero.sqlite").is_file():
            return base.resolve()
        nested = base / "zotero"
        if (nested / "zotero.sqlite").is_file():
            return nested.resolve()

    raise ZoteroReaderError(
        "Cannot locate zotero.sqlite. Set ZOTERO_DATA_DIR or pass --data-dir."
    )


def resolve_zotero_sqlite_path(data_dir: str | Path | None = None) -> Path:
    root = resolve_zotero_data_dir(data_dir)
    return root / "zotero.sqlite"


def _fetch_field_map(conn: sqlite3.Connection, item_id: int) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT f.fieldName, idv.value
        FROM itemData id
        JOIN fields f ON id.fieldID = f.fieldID
        JOIN itemDataValues idv ON id.valueID = idv.valueID
        WHERE id.itemID = ?
        ORDER BY f.fieldName
        """,
        (item_id,),
    ).fetchall()
    return {str(r["fieldName"]): str(r["value"]) for r in rows}


def _fetch_creators(conn: sqlite3.Connection, item_id: int) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT c.firstName, c.lastName, ct.creatorType
        FROM itemCreators ic
        JOIN creators c ON ic.creatorID = c.creatorID
        JOIN creatorTypes ct ON ic.creatorTypeID = ct.creatorTypeID
        WHERE ic.itemID = ?
        ORDER BY ic.orderIndex
        """,
        (item_id,),
    ).fetchall()
    out: list[dict[str, str]] = []
    for r in rows:
        first = str(r["firstName"] or "").strip()
        last = str(r["lastName"] or "").strip()
        name = " ".join(part for part in (first, last) if part).strip()
        out.append({"name": name, "role": str(r["creatorType"])})
    return out


def _fetch_tags(conn: sqlite3.Connection, item_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT t.name
        FROM itemTags it
        JOIN tags t ON it.tagID = t.tagID
        WHERE it.itemID = ?
        ORDER BY t.name
        """,
        (item_id,),
    ).fetchall()
    return [str(r["name"]) for r in rows]


def _fetch_notes(conn: sqlite3.Connection, item_id: int) -> list[dict[str, Any]]:
    schema = _detect_schema(conn)
    if schema.has_item_notes:
        rows = conn.execute(
            """
            SELECT i.itemID, i.key, n.note AS note_html
            FROM itemNotes n
            JOIN items i ON n.itemID = i.itemID
            WHERE n.parentItemID = ?
            ORDER BY i.itemID
            """,
            (item_id,),
        ).fetchall()
    elif schema.items_has_parent_item_id:
        rows = conn.execute(
            """
            SELECT i.itemID, i.key, idv.value AS note_html
            FROM items i
            JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
            JOIN itemData id ON i.itemID = id.itemID
            JOIN fields f ON id.fieldID = f.fieldID AND f.fieldName = 'note'
            JOIN itemDataValues idv ON id.valueID = idv.valueID
            WHERE it.typeName = 'note' AND i.parentItemID = ?
            ORDER BY i.itemID
            """,
            (item_id,),
        ).fetchall()
    else:
        rows = []
    notes: list[dict[str, Any]] = []
    for r in rows:
        html = str(r["note_html"] or "")
        notes.append(
            {
                "item_id": int(r["itemID"]),
                "key": str(r["key"]),
                "text": _strip_html(html),
                "html": html,
            }
        )
    return notes


def _fetch_annotations(conn: sqlite3.Connection, item_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT ann.type, ann.text, ann.comment, ann.color, ann.pageLabel
        FROM itemAttachments att
        JOIN itemAnnotations ann ON ann.parentItemID = att.itemID
        WHERE att.parentItemID = ?
        ORDER BY ann.sortIndex
        """,
        (item_id,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        type_id = int(r["type"])
        out.append(
            {
                "type": _ANNOTATION_TYPES.get(type_id, str(type_id)),
                "text": str(r["text"] or ""),
                "comment": str(r["comment"] or ""),
                "color": str(r["color"] or ""),
                "page_label": str(r["pageLabel"] or ""),
            }
        )
    return out


def resolve_llm_wiki_content_roots(workspace: Path | None = None) -> tuple[Path, Path, Path] | None:
    """Return ``(llm_wiki_root, markdown_root, pdf_root)`` when discoverable."""
    candidates: list[Path] = []
    env = os.environ.get("LLM_WIKI_ROOT", "").strip()
    if env:
        candidates.append(Path(env).expanduser())

    if workspace is not None:
        from firefly.skills.wiki.scripts.llm_wiki_paths import resolve_llm_wiki_root

        wiki_root = resolve_llm_wiki_root(workspace.expanduser().resolve())
        if wiki_root is not None:
            candidates.append(wiki_root)

    here = Path(__file__).resolve()
    for base in here.parents:
        candidates.extend((base / "llm-wiki", base / "backend" / "llm-wiki"))

    seen: set[str] = set()
    for root in candidates:
        try:
            resolved = root.expanduser().resolve()
        except OSError:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        markdown_root = resolved / "raw" / "markdown"
        pdf_root = resolved / "raw" / "pdf"
        if markdown_root.is_dir() or pdf_root.is_dir():
            return resolved, markdown_root, pdf_root
    return None


def _glob_literature_by_key(root: Path, key: str, suffix: str) -> list[Path]:
    if not key or not root.is_dir():
        return []
    matches = [
        p
        for p in root.rglob(f"{key}_*{suffix}")
        if p.is_file()
    ]
    return sorted(matches, key=lambda p: str(p).lower())


def _read_markdown_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:_MAX_STORAGE_TEXT_CHARS]
    except OSError:
        return None


def resolve_literature_content(
    *,
    attachments: list[dict[str, Any]],
    lookup_keys: list[str],
    workspace: Path | None,
    data_dir: Path,
    include_text: bool,
) -> dict[str, Any]:
    """Resolve full-text literature with llm-wiki markdown → pdf → Zotero storage priority."""
    result: dict[str, Any] = {"source": None, "path": None, "text": None}
    keys = [k.strip() for k in lookup_keys if k and k.strip()]
    seen_keys: set[str] = set()
    ordered_keys: list[str] = []
    for key in keys:
        upper = key.upper()
        if upper not in seen_keys:
            seen_keys.add(upper)
            ordered_keys.append(key)

    roots = resolve_llm_wiki_content_roots(workspace)
    if roots is not None:
        _, markdown_root, pdf_root = roots
        for key in ordered_keys:
            for md_path in _glob_literature_by_key(markdown_root, key, ".md"):
                result["source"] = "llm-wiki/markdown"
                result["path"] = str(md_path)
                if include_text:
                    result["text"] = _read_markdown_file(md_path)
                return result

        for key in ordered_keys:
            for pdf_path in _glob_literature_by_key(pdf_root, key, ".pdf"):
                from firefly.skills.markdown.scripts.rag_paths import llm_wiki_markdown_mirror_for_pdf

                md_mirror = llm_wiki_markdown_mirror_for_pdf(pdf_path)
                if md_mirror is not None:
                    result["source"] = "llm-wiki/markdown"
                    result["path"] = str(md_mirror)
                    if include_text:
                        result["text"] = _read_markdown_file(md_mirror)
                    return result
                result["source"] = "llm-wiki/pdf"
                result["path"] = str(pdf_path)
                if include_text:
                    result["text"] = _read_storage_file(pdf_path, "application/pdf")
                return result

    storage_root = data_dir / "storage"
    for att in attachments:
        rel = str(att.get("path") or "").strip()
        if not rel:
            continue
        abs_path = storage_root / rel
        if not abs_path.is_file():
            continue
        content_type = str(att.get("content_type") or "")
        result["source"] = "zotero/storage"
        result["path"] = str(abs_path)
        if include_text:
            result["text"] = _read_storage_file(abs_path, content_type)
        return result

    return result


def _read_storage_file(path: Path, content_type: str) -> str | None:
    if not path.is_file():
        return None
    ctype = (content_type or "").lower()
    if ctype.startswith("text/") or path.suffix.lower() in {".txt", ".md", ".html", ".htm"}:
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:_MAX_STORAGE_TEXT_CHARS]
        except OSError:
            return None
    if ctype == "application/pdf" or path.suffix.lower() == ".pdf":
        try:
            import fitz  # pymupdf
        except ImportError:
            return None
        try:
            doc = fitz.open(str(path))
            parts: list[str] = []
            total = 0
            for page in doc:
                chunk = page.get_text("text")
                if not chunk:
                    continue
                parts.append(chunk)
                total += len(chunk)
                if total >= _MAX_STORAGE_TEXT_CHARS:
                    break
            doc.close()
            return "\n".join(parts)[:_MAX_STORAGE_TEXT_CHARS]
        except Exception:
            return None
    return None


def _fetch_attachments(
    conn: sqlite3.Connection,
    item_id: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT ia.itemID, ia.path, ia.contentType, ia.linkMode, i.key AS attachment_key
        FROM itemAttachments ia
        JOIN items i ON ia.itemID = i.itemID
        WHERE ia.parentItemID = ?
        ORDER BY ia.itemID
        """,
        (item_id,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        rel = str(r["path"] or "")
        content_type = str(r["contentType"] or "")
        out.append(
            {
                "item_id": int(r["itemID"]),
                "key": str(r["attachment_key"] or ""),
                "path": rel,
                "content_type": content_type,
                "link_mode": int(r["linkMode"]),
            }
        )
    return out


def _resolve_item_id(
    conn: sqlite3.Connection,
    *,
    item_id: int | None,
    item_key: str | None,
) -> int:
    if item_id is not None:
        row = conn.execute(
            "SELECT itemID FROM items WHERE itemID = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            raise ZoteroReaderError(f"item_id {item_id} not found")
        return int(row["itemID"])
    if item_key:
        row = conn.execute(
            "SELECT itemID FROM items WHERE key = ?",
            (item_key.strip(),),
        ).fetchone()
        if row is None:
            raise ZoteroReaderError(f"item_key {item_key!r} not found")
        return int(row["itemID"])
    raise ZoteroReaderError("Provide item_id or item_key")


def read_item(
    *,
    item_id: int | None = None,
    item_key: str | None = None,
    include_storage_text: bool = False,
    data_dir: str | Path | None = None,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Return one bibliographic item with fields, creators, notes, and annotations."""
    root = resolve_zotero_data_dir(data_dir)
    db_path = root / "zotero.sqlite"
    ws = Path(workspace).expanduser().resolve() if workspace else None
    with _ro_connect(db_path) as conn:
        resolved_id = _resolve_item_id(conn, item_id=item_id, item_key=item_key)
        meta = conn.execute(
            """
            SELECT i.itemID, i.key, it.typeName
            FROM items i
            JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
            WHERE i.itemID = ?
            """,
            (resolved_id,),
        ).fetchone()
        if meta is None:
            raise ZoteroReaderError(f"item {resolved_id} not found")

        fields = _fetch_field_map(conn, resolved_id)
        attachments = _fetch_attachments(conn, resolved_id)
        lookup_keys = [str(meta["key"])] + [
            str(att["key"]) for att in attachments if att.get("key")
        ]
        literature = resolve_literature_content(
            attachments=attachments,
            lookup_keys=lookup_keys,
            workspace=ws,
            data_dir=root,
            include_text=include_storage_text,
        )
        return {
            "item_id": resolved_id,
            "key": str(meta["key"]),
            "item_type": str(meta["typeName"]),
            "fields": fields,
            "creators": _fetch_creators(conn, resolved_id),
            "tags": _fetch_tags(conn, resolved_id),
            "notes": _fetch_notes(conn, resolved_id),
            "annotations": _fetch_annotations(conn, resolved_id),
            "attachments": attachments,
            "literature": literature,
            "data_dir": str(root),
        }


def search_items(
    query: str,
    *,
    limit: int = 5,
    data_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Search bibliographic items by title, field text, creator, or tag."""
    q = (query or "").strip()
    if not q:
        return []

    root = resolve_zotero_data_dir(data_dir)
    db_path = root / "zotero.sqlite"
    like = f"%{q}%"
    cap = max(1, min(int(limit), 20))

    with _ro_connect(db_path) as conn:
        schema = _detect_schema(conn)
        top_level = _top_level_bib_filter(schema)
        rows = conn.execute(
            f"""
            SELECT DISTINCT i.itemID
            FROM items i
            JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
            LEFT JOIN itemData id ON i.itemID = id.itemID
            LEFT JOIN itemDataValues idv ON id.valueID = idv.valueID
            LEFT JOIN itemCreators ic ON i.itemID = ic.itemID
            LEFT JOIN creators c ON ic.creatorID = c.creatorID
            LEFT JOIN itemTags itg ON i.itemID = itg.itemID
            LEFT JOIN tags t ON itg.tagID = t.tagID
            WHERE {top_level}it.typeName IN ({{placeholders}})
              AND (
                    idv.value LIKE ?
                 OR c.firstName LIKE ?
                 OR c.lastName LIKE ?
                 OR t.name LIKE ?
              )
            ORDER BY i.dateModified DESC
            LIMIT ?
            """.format(placeholders=", ".join("?" for _ in _BIB_ITEM_TYPES)),
            (
                *sorted(_BIB_ITEM_TYPES),
                like,
                like,
                like,
                like,
                cap,
            ),
        ).fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            item_id = int(row["itemID"])
            fields = _fetch_field_map(conn, item_id)
            meta = conn.execute(
                """
                SELECT i.key, it.typeName
                FROM items i
                JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
                WHERE i.itemID = ?
                """,
                (item_id,),
            ).fetchone()
            results.append(
                {
                    "item_id": item_id,
                    "key": str(meta["key"]) if meta else "",
                    "item_type": str(meta["typeName"]) if meta else "",
                    "title": fields.get("title", ""),
                    "date": fields.get("date", ""),
                    "creators": _fetch_creators(conn, item_id),
                    "tags": _fetch_tags(conn, item_id),
                }
            )
        return results


def format_item_for_agent(payload: dict[str, Any]) -> str:
    """Compact human-readable summary for tool output."""
    fields = payload.get("fields") or {}
    lines = [
        f"item_id={payload.get('item_id')} key={payload.get('key')} type={payload.get('item_type')}",
    ]
    title = fields.get("title")
    if title:
        lines.append(f"title: {title}")
    for key in ("date", "DOI", "url", "publicationTitle", "abstractNote"):
        val = fields.get(key)
        if val:
            lines.append(f"{key}: {val}")

    creators = payload.get("creators") or []
    if creators:
        names = ", ".join(c["name"] for c in creators if c.get("name"))
        if names:
            lines.append(f"creators: {names}")

    tags = payload.get("tags") or []
    if tags:
        lines.append("tags: " + ", ".join(tags))

    notes = payload.get("notes") or []
    if notes:
        lines.append("notes:")
        for note in notes:
            text = str(note.get("text") or "").strip()
            if text:
                lines.append(f"- {text[:2000]}")

    annotations = payload.get("annotations") or []
    if annotations:
        lines.append("annotations:")
        for ann in annotations[:30]:
            page = ann.get("page_label") or "?"
            text = str(ann.get("text") or "").strip()
            comment = str(ann.get("comment") or "").strip()
            snippet = text or comment
            if snippet:
                lines.append(f"- p.{page} ({ann.get('type')}): {snippet[:500]}")

    literature = payload.get("literature") or {}
    if literature.get("source"):
        lines.append(f"literature_source: {literature['source']}")
    if literature.get("path"):
        lines.append(f"literature_path: {literature['path']}")
    if literature.get("text"):
        lines.append("literature_text:")
        lines.append(str(literature["text"])[:8000])
    return "\n".join(lines)


def format_search_for_agent(items: list[dict[str, Any]], query: str) -> str:
    if not items:
        return f"No Zotero items matched: {query}"
    lines = [f"Zotero search: {query}", ""]
    for idx, item in enumerate(items, 1):
        creators = ", ".join(c["name"] for c in item.get("creators") or [] if c.get("name"))
        lines.append(
            f"{idx}. [{item.get('item_id')}] {item.get('title') or '(no title)'} "
            f"({item.get('date') or 'n/a'})"
        )
        if creators:
            lines.append(f"   creators: {creators}")
        if item.get("tags"):
            lines.append("   tags: " + ", ".join(item["tags"]))
        lines.append(f"   key: {item.get('key')}")
    return "\n".join(lines)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read local Zotero library as JSON.")
    parser.add_argument("--data-dir", help="Zotero data directory (contains zotero.sqlite)")
    parser.add_argument("--item-id", type=int, help="Numeric Zotero item ID")
    parser.add_argument("--item-key", help="8-char Zotero item key")
    parser.add_argument("--query", help="Search query")
    parser.add_argument("--limit", type=int, default=5, help="Search result limit (default 5)")
    parser.add_argument(
        "--include-storage-text",
        action="store_true",
        help="Include extracted text from linked attachments when possible",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    try:
        if args.query:
            payload = search_items(
                args.query,
                limit=args.limit,
                data_dir=args.data_dir,
            )
        else:
            payload = read_item(
                item_id=args.item_id,
                item_key=args.item_key,
                include_storage_text=args.include_storage_text,
                data_dir=args.data_dir,
            )
    except ZoteroReaderError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
