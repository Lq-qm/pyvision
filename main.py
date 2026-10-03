#!/usr/bin/env python3
"""
pyvision — quebra um PDF em imagens (uma por página) e faz OCR de cada página.

Bibliotecas Python (gerenciadas via uv):
  - pdf2image      → converte páginas do PDF em imagens (usa poppler/pdftoppm)
  - pytesseract    → extrai o texto das imagens (usa o binário do tesseract)
  - pillow

Dependências de sistema necessárias:
  - poppler-utils  (pdftoppm)
  - tesseract-ocr  (tesseract) + arquivos de idioma (.traineddata)

Exemplos:
  uv run main.py documento.pdf
  uv run main.py documento.pdf -o saida --dpi 400
  uv run main.py documento.pdf --pages 1-5,8 --lang por+eng
  uv run main.py documento.pdf --no-ocr
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pdf2image
import pytesseract
from pdf2image.exceptions import (
    PDFInfoNotInstalledError,
    PDFPageCountError,
    PDFPopplerTimeoutError,
    PDFSyntaxError,
    PopplerNotInstalledError,
)
from pytesseract import TesseractError, TesseractNotFoundError

SCRIPT_DIR = Path(__file__).resolve().parent
LOCAL_TESSDATA = SCRIPT_DIR / "tessdata"
IMAGE_FORMATS = {"png": ".png", "jpg": ".jpg", "jpeg": ".jpg", "tif": ".tiff", "tiff": ".tiff"}


class PyvisionError(Exception):
    """Erro fatal com mensagem amigável para o usuário."""


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------- CLI --


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pyvision",
        description="Quebra um PDF em imagens (uma por página) e faz OCR de cada página.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "exemplos:\n"
            "  uv run main.py documento.pdf\n"
            "  uv run main.py documento.pdf -o saida --dpi 400 --pages 1-5,8\n"
            "  uv run main.py documento.pdf --no-ocr\n"
        ),
    )
    parser.add_argument("pdf", help="caminho do arquivo PDF de origem")
    parser.add_argument(
        "-o", "--output-dir", default=None,
        help="diretório de saída das imagens e textos (padrão: <nome-do-pdf>_imagens)",
    )
    parser.add_argument("--dpi", type=int, default=300, help="resolução de renderização (pontos por polegada)")
    parser.add_argument(
        "-f", "--format", default="png", choices=sorted(IMAGE_FORMATS), dest="img_format",
        help="formato das imagens de saída",
    )
    parser.add_argument("--pages", default=None, help='seleção de páginas, ex.: "1-5,8" (1-indexadas)')
    parser.add_argument(
        "-l", "--lang", default="por",
        help="idioma(s) do tesseract, ex.: por, eng ou por+eng",
    )
    parser.add_argument("--tessdata-dir", default=None, help="diretório alternativo com os .traineddata")
    parser.add_argument("--timeout", type=int, default=60, help="timeout do OCR por página (s; 0 = sem limite)")
    parser.add_argument("--no-ocr", action="store_true", help="somente extrair imagens, sem OCR")
    parser.add_argument("-q", "--quiet", action="store_true", help="não mostrar progresso página a página")
    return parser.parse_args(argv)


def parse_pages(spec: str | None, total: int) -> list[int]:
    """Converte especificação como "1-5,8" em lista de números de página (1-indexados)."""
    if spec is None:
        return list(range(1, total + 1))
    selected: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise PyvisionError(f'especificação de página inválida "{part}" (use ex.: 1-5,8)')
        start, end = int(m.group(1)), int(m.group(2) or m.group(1))
        if start < 1 or end < start:
            raise PyvisionError(f'intervalo de páginas inválido "{part}"')
        if start > total or end > total:
            raise PyvisionError(f"página fora do documento: \"{part}\" (o PDF tem {total} páginas)")
        selected.update(range(start, min(end, total) + 1))
    if not selected:
        raise PyvisionError("seleção de páginas vazia")
    return sorted(selected)


# ------------------------------------------------------- dependências ------


def require_binary(name: str, install_hint: str) -> str:
    exe = shutil.which(name)
    if not exe:
        raise PyvisionError(f"'{name}' não encontrado no sistema. {install_hint}")
    return exe


def system_tesseract_languages() -> set[str]:
    try:
        out = subprocess.run(
            ["tesseract", "--list-langs"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return {ln.strip() for ln in out.stdout.splitlines()[1:] if ln.strip()}


def resolve_tessdata_dir(langs: list[str], explicit: str | None) -> str | None:
    """Retorna um diretório .traineddata alternativo, se o sistema não tiver o idioma."""
    if explicit:
        d = Path(explicit).expanduser().resolve()
        if not d.is_dir():
            raise PyvisionError(f"diretório --tessdata-dir não existe: {d}")
        return str(d)
    missing = [l for l in langs if l not in system_tesseract_languages()]
    if (
        missing
        and LOCAL_TESSDATA.is_dir()
        and all((LOCAL_TESSDATA / f"{l}.traineddata").is_file() for l in langs)
    ):
        log(f"noto: idioma(s) {', '.join(missing)} ausente(s) no sistema; usando a pasta local {LOCAL_TESSDATA}")
        return str(LOCAL_TESSDATA)
    return None


# ----------------------------------------------------------------- fluxo --


@dataclass
class PageResult:
    number: int
    image_path: Path
    text_path: Path | None = None
    text: str = ""
    error: str | None = None


def process(args: argparse.Namespace) -> int:
    pdf_path = Path(args.pdf).expanduser()
    if not pdf_path.is_file():
        raise PyvisionError(f"PDF não encontrado: {pdf_path}")

    require_binary("pdftoppm", "instale o poppler-utils (ex.: sudo pacman -S poppler-utils).")

    langs = [l.strip() for l in args.lang.split("+") if l.strip()]
    if not langs:
        raise PyvisionError("idioma(--lang) inválido")

    tesseract_exe: str | None = None
    tessdata_dir: str | None = None
    if not args.no_ocr:
        tesseract_exe = require_binary(
            "tesseract", "instale o tesseract-ocr (ex.: sudo pacman -S tesseract tesseract-ocr-por)."
        )
        tessdata_dir = resolve_tessdata_dir(langs, args.tessdata_dir)
        if tessdata_dir:
            # o tesseract localiza os .traineddata via TESSDATA_PREFIX
            os.environ["TESSDATA_PREFIX"] = str(Path(tessdata_dir).resolve())

    out_dir = (
        Path(args.output_dir).expanduser()
        if args.output_dir
        else pdf_path.with_name(f"{pdf_path.stem}_imagens")
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    log("lendo PDF...")
    try:
        info = pdf2image.pdfinfo_from_path(str(pdf_path))
    except (PopplerNotInstalledError, PDFInfoNotInstalledError) as exc:
        raise PyvisionError(
            "poppler (pdftoppm/pdfinfo) indisponível. "
            "Instale o poppler-utils (ex.: sudo pacman -S poppler-utils)."
        ) from exc
    except (PDFSyntaxError, PDFPageCountError) as exc:
        raise PyvisionError(f"não foi possível ler o PDF: {exc}") from exc
    except PDFPopplerTimeoutError as exc:
        raise PyvisionError("poppler estourou o timeout ao ler o PDF") from exc

    total = int(info.get("PageCount") or info.get("Pages") or 0)
    if total == 0:
        raise PyvisionError("o PDF não possui páginas")

    pages = parse_pages(args.pages, total)

    log(f"renderizando página(s) {pages[0]}–{pages[-1]} em {args.dpi} dpi...")
    try:
        images = pdf2image.convert_from_path(
            str(pdf_path), dpi=args.dpi, first_page=pages[0], last_page=pages[-1]
        )
    except Exception as exc:  # RuntimeError etc. vindo do poppler
        raise PyvisionError(f"erro ao converter PDF em imagens: {exc}") from exc
    ext = IMAGE_FORMATS[args.img_format]
    save_kwargs = {"quality": 95} if ext == ".jpg" else {}

    log(f"processando {len(pages)} de {total} páginas → {out_dir}")
    results: list[PageResult] = []
    for idx, n in enumerate(pages, start=1):
        img = images[n - pages[0]]
        image_path = out_dir / f"page_{n:03d}{ext}"
        img.save(image_path, **save_kwargs)
        result = PageResult(number=n, image_path=image_path)

        if tesseract_exe:
            try:
                text = pytesseract.image_to_string(
                    img,
                    lang="+".join(langs),
                    timeout=args.timeout,
                )
            except TesseractNotFoundError as exc:
                raise PyvisionError(f"tesseract não encontrado: {exc}") from exc
            except TesseractError as exc:
                result.error = str(exc).strip()
                log(f"  [{idx}/{len(pages)}] página {n}: IMAGEM ok, FALHA no OCR — {result.error}")
                results.append(result)
                continue
            text = text.strip()
            result.text = text + "\n" if text else ""
            text_path = out_dir / f"page_{n:03d}.txt"
            text_path.write_text(result.text, encoding="utf-8")
            result.text_path = text_path
            if not args.quiet:
                log(f"  [{idx}/{len(pages)}] página {n}: {image_path.name} + OCR ({len(result.text)} chars)")
        elif not args.quiet:
            log(f"  [{idx}/{len(pages)}] página {n}: {image_path.name}")

        results.append(result)

    # Texto combinado de todas as páginas
    combined: Path | None = None
    if tesseract_exe:
        parts = [f"===== PÁGINA {r.number} =====\n{r.text.rstrip()}\n" for r in results if r.error is None]
        combined = out_dir / "ocr_completo.txt"
        combined.write_text("\n".join(parts), encoding="utf-8")

    log("")
    log(f"concluído: {len(results)} página(s) → {out_dir}")
    log(f"  imagens: {ext} (uma por página)")
    if tesseract_exe:
        ok = [r for r in results if r.error is None]
        log(f"  OCR: {sum(len(r.text) for r in ok)} chars no total (por página em .txt + {combined.name})")
    failed = [r.number for r in results if r.error]
    if failed:
        log(f"  AVISO: OCR falhou nas página(s): {', '.join(map(str, failed))}")
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return process(args)
    except PyvisionError as exc:
        log(f"erro: {exc}")
        return 1
    except KeyboardInterrupt:
        log("\ninterrompido pelo usuário.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
