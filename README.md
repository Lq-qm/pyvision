# pyvision

Quebra um PDF (escaneado/imagem) em imagens — **uma por página** — e faz **OCR** de cada página.

- `pdf2image` (poppler/pdftoppm) → converte as páginas do PDF em imagens
- `pytesseract` (tesseract-ocr) → extrai o texto de cada imagem

## Dependências de sistema

| Pacote | Por quê |
|---|---|
| `poppler-utils` (`pdftoppm`) | usado pela `pdf2image` para renderizar as páginas |
| `tesseract-ocr` | usado pela `pytesseract` para o OCR |
| idiomas `.traineddata` | ex.: `tesseract-ocr-por`, `tesseract-ocr-eng` |

No Arch/EndeavourOS:

```bash
sudo pacman -S poppler-utils tesseract tesseract-ocr-por tesseract-ocr-eng
```

> Se o idioma não estiver instalado no sistema, o script procura automaticamente
> na pasta local `tessdata/` (os arquivos `eng` e `por` já vêm inclusos) e usa a
> variável `TESSDATA_PREFIX` para apontá-la ao tesseract.

## Instalação (uv)

```bash
uv sync            # cria o .venv e instala as dependências Python
```

## Uso

```bash
# todas as páginas (300 dpi, idioma por, PNG)
uv run pyvision documento.pdf

# saída em pasta específica, 400 dpi
uv run pyvision documento.pdf -o saida --dpi 400

# apenas algumas páginas (1-indexadas), JPG
uv run pyvision documento.pdf --pages 1-5,8 -f jpg

# somente imagens, sem OCR
uv run pyvision documento.pdf --no-ocr

# múltiplos idiomas
uv run pyvision documento.pdf -l por+eng
```

Também funciona com `uv run main.py ...` (o `main.py` é o mesmo script).

### Opções

| Opção | Padrão | Descrição |
|---|---|---|
| `-o, --output-dir` | `<nome-do-pdf>_imagens` | diretório de saída |
| `--dpi` | `300` | resolução de renderização |
| `-f, --format` | `png` | `png`, `jpg`, `tiff` |
| `--pages` | todas | seleção de páginas, ex.: `1-5,8` |
| `-l, --lang` | `por` | idioma(s) do tesseract, ex.: `por+eng` |
| `--tessdata-dir` | — | diretório alternativo de `.traineddata` |
| `--timeout` | `60` | timeout do OCR por página (s; `0` = sem limite) |
| `--no-ocr` | — | só extrai imagens |
| `-q, --quiet` | — | sem progresso página a página |

## Saída

Para cada página `N` no diretório de saída:

- `page_NNN.png` — a imagem da página
- `page_NNN.txt` — o texto extraído por OCR
- `ocr_completo.txt` — texto de todas as páginas, com separadores

## Código de saída

- `0` sucesso
- `1` erro de uso/leitura (PDF inexistente, binário ausente, página inválida…)
- `2` imagens geradas, mas o OCR falhou em algumas páginas
