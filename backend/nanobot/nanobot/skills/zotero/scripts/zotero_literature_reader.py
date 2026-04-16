#!/usr/bin/env python3
"""
Read literature content from a local Zotero profile.

This script extracts:
- item metadata (title, year, DOI, etc.)
- child notes
- PDF annotations (if any)
- optional plain-text sidecar files under storage/

Examples:
  python zotero_literature_reader.py --item-key ABCD1234
  python zotero_literature_reader.py --item-id 12345 --include-storage-text
  python zotero_literature_reader.py --query "attention is all you need" --limit 3
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


DEFAULT_ZOTERO_DIR = Path.home() / "Zotero"
MAX_TEXT_CHARS = 30000


def _open_db(zotero_dir: Path) -> sqlite3.Connection:
    db_path = zotero_dir / "zotero.sqlite"
    if not db_path.exists():
        raise FileNotFoundError(f"zotero.sqlite not found: {db_path}")
    # read-only connection
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_item_by_id(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT itemID, key, itemTypeID, libraryID
        FROM items
        WHERE itemID = ? AND itemID NOT IN (SELECT itemID FROM deletedItems)
        """,
        (item_id,),
    ).fetchone()


def _fetch_item_by_key(conn: sqlite3.Connection, item_key: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT itemID, key, itemTypeID, libraryID
        FROM items
        WHERE key = ? AND itemID NOT IN (SELECT itemID FROM deletedItems)
        """,
        (item_key,),
    ).fetchone()


def _search_top_items(conn: sqlite3.Connection, query: str, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT i.itemID, i.key, i.itemTypeID, i.libraryID
        FROM items i
        JOIN itemData id ON id.itemID = i.itemID
        JOIN itemDataValues v ON v.valueID = id.valueID
        WHERE i.itemID NOT IN (SELECT itemID FROM deletedItems)
          AND i.itemID NOT IN (SELECT itemID FROM itemNotes WHERE parentItemID IS NOT NULL)
          AND v.value LIKE ?
        GROUP BY i.itemID
        ORDER BY i.itemID DESC
        LIMIT ?
        """,
        (f"%{query}%", limit),
    ).fetchall()


def _item_fields(conn: sqlite3.Connection, item_id: int) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT f.fieldName, v.value
        FROM itemData id
        JOIN fields f ON f.fieldID = id.fieldID
        JOIN itemDataValues v ON v.valueID = id.valueID
        WHERE id.itemID = ?
        """,
        (item_id,),
    ).fetchall()
    return {str(r["fieldName"]): str(r["value"]) for r in rows}


def _child_notes(conn: sqlite3.Connection, parent_item_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT n.note
        FROM itemNotes n
        JOIN items i ON i.itemID = n.itemID
        WHERE n.parentItemID = ?
          AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
        ORDER BY n.itemID ASC
        """,
        (parent_item_id,),
    ).fetchall()
    notes: list[str] = []
    for r in rows:
        note = str(r["note"] or "").strip()
        if note:
            notes.append(note)
    return notes


def _annotations(conn: sqlite3.Connection, parent_item_id: int) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT ia.text, ia.comment, ia.color, ia.pageLabel, i.key
        FROM itemAnnotations ia
        JOIN itemAttachments a ON a.itemID = ia.parentItemID
        JOIN items i ON i.itemID = ia.itemID
        WHERE a.parentItemID = ?
          AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
        ORDER BY ia.itemID ASC
        """,
        (parent_item_id,),
    ).fetchall()
    out: list[dict[str, str]] = []
    for r in rows:
        out.append(
            {
                "annotation_key": str(r["key"] or ""),
                "text": str(r["text"] or ""),
                "comment": str(r["comment"] or ""),
                "color": str(r["color"] or ""),
                "page_label": str(r["pageLabel"] or ""),
            }
        )
    return out


def _attachment_keys(conn: sqlite3.Connection, parent_item_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT i.key
        FROM itemAttachments a
        JOIN items i ON i.itemID = a.itemID
        WHERE a.parentItemID = ?
          AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
        ORDER BY i.itemID ASC
        """,
        (parent_item_id,),
    ).fetchall()
    return [str(r["key"]) for r in rows if r["key"]]


def _storage_text(zotero_dir: Path, attachment_keys: list[str], max_chars: int) -> dict[str, str]:
    storage_dir = zotero_dir / "storage"
    result: dict[str, str] = {}
    if not storage_dir.exists():
        return result
    for key in attachment_keys:
        folder = storage_dir / key
        if not folder.exists():
            continue
        for candidate in ("fulltext.txt", "text.txt"):
            path = folder / candidate
            if path.exists():
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                    result[key] = text[:max_chars]
                    break
                except Exception:
                    continue
    return result


def _build_record(
    conn: sqlite3.Connection,
    zotero_dir: Path,
    item_row: sqlite3.Row,
    include_storage_text: bool,
    max_chars: int,
) -> dict:
    item_id = int(item_row["itemID"])
    fields = _item_fields(conn, item_id)
    notes = _child_notes(conn, item_id)
    annos = _annotations(conn, item_id)
    attach_keys = _attachment_keys(conn, item_id)

    record = {
        "item_id": item_id,
        "item_key": str(item_row["key"]),
        "library_id": int(item_row["libraryID"] or 0),
        "fields": fields,
        "notes": notes,
        "annotations": annos,
        "attachment_keys": attach_keys,
    }
    if include_storage_text:
        record["storage_text"] = _storage_text(zotero_dir, attach_keys, max_chars)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Read Zotero literature content for agent context.")
    parser.add_argument("--zotero-dir", default=str(DEFAULT_ZOTERO_DIR), help="Zotero profile directory")
    parser.add_argument("--item-id", type=int, default=None, help="Target Zotero itemID")
    parser.add_argument("--item-key", default=None, help="Target Zotero item key")
    parser.add_argument("--query", default=None, help="Fallback keyword search when item is not specified")
    parser.add_argument("--limit", type=int, default=1, help="Max items when using --query")
    parser.add_argument(
        "--include-storage-text",
        action="store_true",
        help="Also read fulltext.txt/text.txt from storage/<attachment_key>/",
    )
    parser.add_argument("--max-text-chars", type=int, default=MAX_TEXT_CHARS)
    args = parser.parse_args()

    zotero_dir = Path(args.zotero_dir).expanduser().resolve()
    conn = _open_db(zotero_dir)
    try:
        items: list[sqlite3.Row] = []
        if args.item_id is not None:
            row = _fetch_item_by_id(conn, args.item_id)
            if row:
                items = [row]
        elif args.item_key:
            row = _fetch_item_by_key(conn, args.item_key.strip())
            if row:
                items = [row]
        elif args.query:
            items = _search_top_items(conn, args.query.strip(), max(1, args.limit))
        else:
            raise SystemExit("One of --item-id, --item-key, or --query is required.")

        output = {
            "zotero_dir": str(zotero_dir),
            "count": len(items),
            "items": [
                _build_record(
                    conn,
                    zotero_dir,
                    row,
                    include_storage_text=bool(args.include_storage_text),
                    max_chars=max(1000, args.max_text_chars),
                )
                for row in items
            ],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
