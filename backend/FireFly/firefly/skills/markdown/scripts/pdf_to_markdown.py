#!/usr/bin/env python3
"""
Convert PDF files to Markdown using pymupdf4llm.

Default mapping:
- PDF root: backend/llm-wiki/raw/pdf
- Markdown root: backend/llm-wiki/raw/markdown

Supports both batch mode and single-file mode.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import traceback
from contextlib import contextmanager
from pathlib import Path

try:
    import pymupdf
    import pymupdf4llm
except ImportError as exc:  # pragma: no cover - runtime dependency
    raise SystemExit(
        "Missing dependency: pymupdf4llm. Install with `pip install pymupdf4llm`."
    ) from exc


DEFAULT_PDF_ROOT = Path("backend/llm-wiki/raw/pdf")
DEFAULT_MARKDOWN_ROOT = Path("backend/llm-wiki/raw/markdown")
DEFAULT_OCR_LANGUAGE = "chi_sim+eng"
# legacy 模式常把一张图拆成多个 pdf-{page}-{index} 小图块；同页 ≥4 张视为碎片化
_FRAGMENTED_PAGE_IMAGE_THRESHOLD = 4
# Windows MAX_PATH 为 260；layout 模式图片名含页码后缀，预留余量
_WINDOWS_SAFE_PATH_LIMIT = 240
_ZOTERO_KEY_RE = re.compile(r"^([A-Z0-9]{8})_")


def _image_basename(stem: str) -> str:
    """短文件名前缀，避免 Windows 路径过长；优先用 Zotero item key。"""
    match = _ZOTERO_KEY_RE.match(stem)
    if match:
        return match.group(1)
    if len(stem) <= 48:
        return stem
    return hashlib.sha256(stem.encode("utf-8")).hexdigest()[:12]


def _worst_case_image_filename(basename: str) -> str:
    return f"{basename}-9999-99.png"


def _joined_path_length(parent: Path, *parts: str) -> int:
    try:
        path = parent.joinpath(*parts).resolve()
    except OSError:
        path = parent.joinpath(*parts)
    return len(str(path))


def _assets_dir_name(markdown_path: Path) -> str:
    """返回 .assets 目录名；路径过长时改用短前缀。"""
    stem = markdown_path.stem
    parent = markdown_path.parent
    long_assets = f"{stem}.assets"
    if (
        _joined_path_length(
            parent, long_assets, _worst_case_image_filename(stem)
        )
        < _WINDOWS_SAFE_PATH_LIMIT
    ):
        return long_assets
    short_base = _image_basename(stem)
    return f"{short_base}.assets"


def assets_dir_path(markdown_path: Path) -> Path:
    return markdown_path.parent / _assets_dir_name(markdown_path)


def _iter_pdf_files(pdf_root: Path) -> list[Path]:
    return sorted(path for path in pdf_root.rglob("*.pdf") if path.is_file())


def _target_markdown_path(pdf_path: Path, pdf_root: Path, markdown_root: Path) -> Path:
    relative = pdf_path.relative_to(pdf_root)
    return (markdown_root / relative).with_suffix(".md")


def _cleanup_partial_markdown_output(
    markdown_path: Path,
    images_dir: Path | None,
) -> None:
    """Remove incomplete outputs after a failed conversion."""
    part_path = markdown_path.with_suffix(".md.part")
    for path in (part_path, markdown_path):
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass
    if images_dir is not None and images_dir.exists():
        try:
            shutil.rmtree(images_dir)
        except OSError:
            pass


def _convert_error_log_path(markdown_path: Path) -> Path:
    return markdown_path.with_suffix(".md.convert-error.txt")


def _write_convert_error_log(markdown_path: Path, exc: BaseException) -> Path:
    log_path = _convert_error_log_path(markdown_path)
    body = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    try:
        log_path.write_text(body, encoding="utf-8")
    except OSError:
        pass
    return log_path


def _convert_lock_path(markdown_path: Path) -> Path:
    return markdown_path.with_suffix(".md.converting")


@contextmanager
def _working_directory(path: Path):
    """仅用于 write_images 时让 legacy 模式写出正确的相对图片路径。"""
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _assets_look_fragmented(assets_dir: Path) -> bool:
    """检测 legacy 模式产生的同页多碎片图（一张图被拆成多块）。"""
    page_counts: dict[str, int] = {}
    pattern = re.compile(r"\.pdf-(\d+)-(\d+)\.")
    for path in assets_dir.iterdir():
        if not path.is_file():
            continue
        match = pattern.search(path.name)
        if match:
            page_counts[match.group(1)] = page_counts.get(match.group(1), 0) + 1
    if not page_counts:
        return False
    return max(page_counts.values()) >= _FRAGMENTED_PAGE_IMAGE_THRESHOLD


def _run_pymupdf4llm_to_markdown(
    pdf_path: Path,
    *,
    write_images: bool,
    images_dir: Path | None,
    use_ocr: bool,
    force_ocr: bool,
    ocr_language: str,
    markdown_parent: Path,
    image_basename: str,
) -> str:
    """调用 pymupdf4llm；write_images 时走 layout 模式整图导出，否则 legacy 文本模式。"""
    to_md_kwargs: dict[str, object] = {
        "write_images": write_images,
        "image_format": "png",
        "use_ocr": use_ocr,
        "force_ocr": force_ocr,
        "ocr_language": ocr_language,
    }
    if write_images and images_dir is not None:
        # layout 模式按 picture 区域渲染整图，避免 legacy 逐块导出碎图
        pymupdf4llm.use_layout(True)
        to_md_kwargs["image_path"] = images_dir.name
        to_md_kwargs["force_text"] = False
        to_md_kwargs["dpi"] = 150
        to_md_kwargs["filename"] = image_basename
        # 从内存打开 PDF，使 pymupdf4llm 使用短 filename 而非完整路径作图片前缀
        pdf_bytes = pdf_path.read_bytes()
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            with _working_directory(markdown_parent):
                return str(pymupdf4llm.to_markdown(doc, **to_md_kwargs))
        finally:
            doc.close()
    pymupdf4llm.use_layout(False)
    to_md_kwargs["image_path"] = ""
    return str(pymupdf4llm.to_markdown(str(pdf_path), **to_md_kwargs))


def _convert_one(
    pdf_path: Path,
    markdown_path: Path,
    force_ocr: bool,
    ocr_language: str,
    *,
    use_ocr: bool = False,
    write_images: bool = False,
) -> None:
    pdf_path = pdf_path.expanduser().resolve()
    markdown_path = markdown_path.expanduser().resolve()
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    image_basename = _image_basename(markdown_path.stem)
    images_dir = assets_dir_path(markdown_path) if write_images else None
    part_path = markdown_path.with_suffix(".md.part")
    lock_path = _convert_lock_path(markdown_path)

    if lock_path.exists():
        raise RuntimeError(
            f"conversion already in progress for {markdown_path.name} "
            f"(lock file: {lock_path})"
        )

    # 仅清理上次失败留下的 .md/.part；write_images=False 时不碰 .assets，避免目录闪烁
    _cleanup_partial_markdown_output(markdown_path, images_dir)
    if write_images and images_dir is not None and images_dir.exists():
        try:
            shutil.rmtree(images_dir)
        except OSError:
            pass
    lock_path.write_text("converting\n", encoding="utf-8")

    try:
        markdown_text = _run_pymupdf4llm_to_markdown(
            pdf_path,
            write_images=write_images,
            images_dir=images_dir,
            use_ocr=use_ocr,
            force_ocr=force_ocr,
            ocr_language=ocr_language,
            markdown_parent=markdown_path.parent,
            image_basename=image_basename,
        )
        if markdown_text is None or not str(markdown_text).strip():
            raise RuntimeError(
                f"pymupdf4llm returned empty markdown for {pdf_path}"
            )
        part_path.write_text(str(markdown_text), encoding="utf-8")
        part_path.replace(markdown_path)
        try:
            _convert_error_log_path(markdown_path).unlink(missing_ok=True)
        except OSError:
            pass
    except Exception as exc:
        _cleanup_partial_markdown_output(markdown_path, images_dir)
        log_path = _write_convert_error_log(markdown_path, exc)
        raise RuntimeError(
            f"PDF to Markdown failed for {pdf_path}: {exc} "
            f"(details: {log_path})"
        ) from exc
    finally:
        pymupdf4llm.use_layout(False)
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def convert_one(
    pdf_path: Path,
    markdown_path: Path,
    overwrite: bool,
    force_ocr: bool,
    ocr_language: str,
    *,
    use_ocr: bool = False,
    write_images: bool = False,
) -> dict[str, object]:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file does not exist: {pdf_path}")
    if not pdf_path.is_file():
        raise ValueError(f"PDF path is not a file: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Input file must be a .pdf: {pdf_path}")

    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    assets_dir = assets_dir_path(markdown_path)
    if markdown_path.exists() and not overwrite:
        needs_images = write_images and (
            not assets_dir.is_dir()
            or not any(assets_dir.iterdir())
            or _assets_look_fragmented(assets_dir)
        )
        if not needs_images:
            return {
                "pdf_path": str(pdf_path),
                "markdown_path": str(markdown_path),
                "status": "skipped",
                "reason": "markdown already exists (use --overwrite to replace)",
            }

    _convert_one(
        pdf_path=pdf_path,
        markdown_path=markdown_path,
        force_ocr=force_ocr,
        ocr_language=ocr_language,
        use_ocr=use_ocr,
        write_images=write_images,
    )
    out: dict[str, object] = {
        "pdf_path": str(pdf_path),
        "markdown_path": str(markdown_path),
        "status": "converted",
        "write_images": write_images,
        "use_ocr": use_ocr,
    }
    if write_images:
        out["images_dir"] = str(assets_dir_path(markdown_path))
    return out


def convert_all(
    pdf_root: Path,
    markdown_root: Path,
    overwrite: bool,
    force_ocr: bool,
    ocr_language: str,
    *,
    use_ocr: bool = False,
    write_images: bool = False,
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
            one_result = convert_one(
                pdf_path=pdf_path,
                markdown_path=markdown_path,
                overwrite=overwrite,
                force_ocr=force_ocr,
                ocr_language=ocr_language,
                use_ocr=use_ocr,
                write_images=write_images,
            )
            if str(one_result.get("status")) == "converted":
                converted.append(rel_pdf)
            else:
                skipped.append(rel_md)
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
        description="Convert PDF files into Markdown (batch or single mode)."
    )
    parser.add_argument(
        "--pdf",
        default="",
        help="Single-file mode: input PDF file path.",
    )
    parser.add_argument(
        "--markdown",
        default="",
        help="Single-file mode: output Markdown file path.",
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
        "--write-images",
        action="store_true",
        help="Export images to <stem>.assets (off by default; slower, causes extra folders).",
    )
    parser.add_argument(
        "--use-ocr",
        action="store_true",
        help="Enable OCR (off by default; slower, for scanned PDFs).",
    )
    parser.add_argument(
        "--force-ocr",
        action="store_true",
        help="Force OCR on every page (implies --use-ocr; for scanned Chinese PDFs).",
    )
    parser.add_argument(
        "--ocr-language",
        default=DEFAULT_OCR_LANGUAGE,
        help="OCR language hint passed to pymupdf4llm, e.g. chi_sim+eng.",
    )
    args = parser.parse_args()

    single_pdf = str(args.pdf).strip()
    single_markdown = str(args.markdown).strip()
    if bool(single_pdf) ^ bool(single_markdown):
        raise SystemExit("Single-file mode requires both --pdf and --markdown.")

    use_ocr = bool(args.use_ocr or args.force_ocr)
    force_ocr = bool(args.force_ocr)
    write_images = bool(args.write_images)
    ocr_language = str(args.ocr_language).strip() or DEFAULT_OCR_LANGUAGE

    if single_pdf and single_markdown:
        pdf_path = Path(single_pdf).expanduser().resolve()
        markdown_path = Path(single_markdown).expanduser().resolve()
        result = convert_one(
            pdf_path=pdf_path,
            markdown_path=markdown_path,
            overwrite=args.overwrite,
            force_ocr=force_ocr,
            ocr_language=ocr_language,
            use_ocr=use_ocr,
            write_images=write_images,
        )
    else:
        pdf_root = Path(args.pdf_root).expanduser().resolve()
        markdown_root = Path(args.markdown_root).expanduser().resolve()
        result = convert_all(
            pdf_root=pdf_root,
            markdown_root=markdown_root,
            overwrite=args.overwrite,
            force_ocr=force_ocr,
            ocr_language=ocr_language,
            use_ocr=use_ocr,
            write_images=write_images,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if single_pdf:
        return 0
    return 0 if not result["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
