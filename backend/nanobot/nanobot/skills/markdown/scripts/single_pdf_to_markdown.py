#!/usr/bin/env python3
"""
Convert one PDF file to one Markdown file using pymupdf4llm.
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

DEFAULT_OCR_LANGUAGE = "chi_sim+eng"

@contextmanager
def _working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def convert_one(
    pdf_path: Path,
    markdown_path: Path,
    overwrite: bool,
    force_ocr: bool,
    ocr_language: str,
) -> dict[str, object]:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file does not exist: {pdf_path}")
    if not pdf_path.is_file():
        raise ValueError(f"PDF path is not a file: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Input file must be a .pdf: {pdf_path}")

    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    if markdown_path.exists() and not overwrite:
        return {
            "pdf_path": str(pdf_path),
            "markdown_path": str(markdown_path),
            "status": "skipped",
            "reason": "markdown already exists (use --overwrite to replace)",
        }

    images_dir = markdown_path.parent / f"{markdown_path.stem}.assets"
    if overwrite and images_dir.exists():
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
    return {
        "pdf_path": str(pdf_path),
        "markdown_path": str(markdown_path),
        "images_dir": str(images_dir),
        "status": "converted",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert one PDF file into one Markdown file.")
    parser.add_argument("--pdf", required=True, help="Input PDF file path.")
    parser.add_argument("--markdown", required=True, help="Output Markdown file path.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite output markdown if it already exists.",
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

    pdf_path = Path(args.pdf).expanduser().resolve()
    markdown_path = Path(args.markdown).expanduser().resolve()

    result = convert_one(
        pdf_path=pdf_path,
        markdown_path=markdown_path,
        overwrite=args.overwrite,
        force_ocr=bool(args.force_ocr),
        ocr_language=str(args.ocr_language).strip() or DEFAULT_OCR_LANGUAGE,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
