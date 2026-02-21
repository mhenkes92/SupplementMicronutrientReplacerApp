from __future__ import annotations

import json
import re
from datetime import datetime, UTC
from pathlib import Path


def resolve_reference_dir(base_dir: Path) -> Path | None:
    candidates = [
        base_dir / "fitness_reference",
        base_dir / "Fitness_reference",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 200) -> list[str]:
    clean = re.sub(r"\s+", " ", (text or "")).strip()
    if not clean:
        return []

    chunks: list[str] = []
    start = 0
    step = max(1, chunk_size - overlap)
    while start < len(clean):
        chunks.append(clean[start : start + chunk_size])
        start += step
    return chunks


def extract_pdf_pages(pdf_path: Path) -> list[str]:
    pages: list[str] = []

    try:
        import fitz  # pymupdf

        with fitz.open(str(pdf_path)) as doc:
            for page in doc:
                try:
                    text = (page.get_text("text") or "").strip()
                except Exception:
                    text = ""
                pages.append(text)
        return pages
    except Exception:
        return []


def build_index(reference_dir: Path, out_jsonl: Path, out_meta: Path) -> dict[str, int | str]:
    pdf_files = sorted(reference_dir.glob("*.pdf"))
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)

    total_chunks = 0
    processed_files = 0
    failed_files = 0

    with out_jsonl.open("w", encoding="utf-8") as out:
        for pdf_path in pdf_files:
            pages = extract_pdf_pages(pdf_path)
            if not pages:
                failed_files += 1
                continue

            chunk_id = 0
            file_had_chunks = False
            for page_no, text in enumerate(pages, start=1):
                if not text:
                    continue
                for chunk in chunk_text(text):
                    if not chunk.strip():
                        continue
                    chunk_id += 1
                    total_chunks += 1
                    file_had_chunks = True
                    out.write(
                        json.dumps(
                            {
                                "source": pdf_path.name,
                                "page": page_no,
                                "chunk_id": chunk_id,
                                "text": chunk,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

            if file_had_chunks:
                processed_files += 1
            else:
                failed_files += 1

    meta = {
        "reference_dir": str(reference_dir),
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "total_pdf_files": len(pdf_files),
        "processed_pdf_files": processed_files,
        "failed_or_empty_pdf_files": failed_files,
        "total_chunks": total_chunks,
        "index_file": str(out_jsonl),
    }
    out_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def main() -> None:
    app_dir = Path(__file__).resolve().parent
    base_dir = app_dir.parent
    reference_dir = resolve_reference_dir(base_dir)
    if reference_dir is None:
        print("ERROR: reference folder not found (fitness_reference/Fitness_reference)")
        raise SystemExit(1)

    out_jsonl = app_dir / "data" / "fitness_rag_chunks.jsonl"
    out_meta = app_dir / "data" / "fitness_rag_meta.json"

    meta = build_index(reference_dir, out_jsonl, out_meta)
    print("RAG index build complete")
    print(f"Processed PDFs: {meta['processed_pdf_files']} / {meta['total_pdf_files']}")
    print(f"Failed/empty PDFs: {meta['failed_or_empty_pdf_files']}")
    print(f"Total chunks: {meta['total_chunks']}")
    print(f"Index file: {out_jsonl}")


if __name__ == "__main__":
    main()
