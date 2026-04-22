#!/usr/bin/env python3
"""
Batch convert PDF files to Markdown using pymupdf4llm.

Default mapping:
- PDF root: backend/llm-wiki/raw/pdf
- Markdown root: backend/llm-wiki/raw/markdown

The output keeps the same relative directory structure as the PDF root.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

try:
    import pymupdf4llm
except ImportError as exc:  # pragma: no cover - runtime dependency
    raise SystemExit(
        "Missing dependency: pymupdf4llm. Install with `pip install pymupdf4llm`."
    ) from exc


DEFAULT_PDF_ROOT = Path("backend/llm-wiki/raw/pdf")
DEFAULT_MARKDOWN_ROOT = Path("backend/llm-wiki/raw/markdown")
DEFAULT_OCR_LANGUAGE = "chi_sim+eng"


def _iter_pdf_files(pdf_root: Path) -> list[Path]:
    return sorted(path for path in pdf_root.rglob("*.pdf") if path.is_file())


def _target_markdown_path(pdf_path: Path, pdf_root: Path, markdown_root: Path) -> Path:
    relative = pdf_path.relative_to(pdf_root)
    return (markdown_root / relative).with_suffix(".md")


@contextmanager
def _working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _convert_one(pdf_path: Path, markdown_path: Path, force_ocr: bool, ocr_language: str) -> None:
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    images_dir = markdown_path.parent / f"{markdown_path.stem}.assets"
    if images_dir.exists():
        shutil.rmtree(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)
    try:
        with _working_directory(markdown_path.parent):
            markdown_text = pymupdf4llm.to_markdown(
                str(pdf_path),
                write_images=True,
                image_path=images_dir.name,
                image_format="png",
                use_ocr=True,
                force_ocr=force_ocr,
                ocr_language=ocr_language,
            )
        markdown_path.write_text(markdown_text, encoding="utf-8")
    except Exception:
        # 避免留下只有图片目录、没有 markdown 的半成品状态。
        if markdown_path.exists():
            markdown_path.unlink()
        if images_dir.exists():
            shutil.rmtree(images_dir)
        raise


def convert_all(
    pdf_root: Path,
    markdown_root: Path,
    overwrite: bool,
    force_ocr: bool,
    ocr_language: str,
) -> dict[str, object]:
    if not pdf_root.exists():
        raise FileNotFoundError(f"PDF root does not exist: {pdf_root}")
    if not pdf_root.is_dir():
        raise NotADirectoryError(f"PDF root is not a directory: {pdf_root}")

    converted: list[str] = []
    skipped: list[str] = []
    failed: list[dict[str, str]] = []

    for pdf_path in _iter_pdf_files(pdf_root):
        markdown_path = _target_markdown_path(pdf_path, pdf_root, markdown_root)
        rel_pdf = str(pdf_path.relative_to(pdf_root))
        rel_md = str(markdown_path.relative_to(markdown_root))

        if markdown_path.exists() and not overwrite:
            skipped.append(rel_md)
            continue

        try:
            _convert_one(pdf_path, markdown_path, force_ocr=force_ocr, ocr_language=ocr_language)
            converted.append(rel_pdf)
        except Exception as exc:  # noqa: BLE001
            failed.append({"pdf": rel_pdf, "error": str(exc)})

    return {
        "pdf_root": str(pdf_root),
        "markdown_root": str(markdown_root),
        "converted": converted,
        "skipped": skipped,
        "failed": failed,
        "count": {
            "converted": len(converted),
            "skipped": len(skipped),
            "failed": len(failed),
            "total_pdf": len(converted) + len(skipped) + len(failed),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert PDFs under llm-wiki/raw/pdf into llm-wiki/raw/markdown."
    )
    parser.add_argument(
        "--pdf-root",
        default=str(DEFAULT_PDF_ROOT),
        help="Root directory that contains PDF files.",
    )
    parser.add_argument(
        "--markdown-root",
        default=str(DEFAULT_MARKDOWN_ROOT),
        help="Root directory for converted Markdown files.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing markdown files.",
    )
    parser.add_argument(
        "--force-ocr",
        action="store_true",
        help="Force OCR for all pages (recommended for scanned Chinese PDFs).",
    )
    parser.add_argument(
        "--ocr-language",
        default=DEFAULT_OCR_LANGUAGE,
        help="OCR language hint passed to pymupdf4llm, e.g. chi_sim+eng.",
    )
    args = parser.parse_args()

    pdf_root = Path(args.pdf_root).expanduser().resolve()
    markdown_root = Path(args.markdown_root).expanduser().resolve()

    result = convert_all(
        pdf_root=pdf_root,
        markdown_root=markdown_root,
        overwrite=args.overwrite,
        force_ocr=bool(args.force_ocr),
        ocr_language=str(args.ocr_language).strip() or DEFAULT_OCR_LANGUAGE,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
