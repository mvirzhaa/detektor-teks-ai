"""Ekstraksi teks dari dokumen yang diunggah (PDF, DOCX, TXT, MD, HTML)."""
import io
import re
from html.parser import HTMLParser

from .features import normalize_text

SUPPORTED = (".txt", ".md", ".pdf", ".docx", ".html", ".htm")


class ExtractError(ValueError):
    pass


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            if enc == "utf-16" and "\x00" in text:
                continue
            return text
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _pdf(data: bytes) -> tuple[str, int]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as e:  # noqa: BLE001
                raise ExtractError("PDF terkunci kata sandi.") from e
        pages = []
        for p in reader.pages:
            pages.append(p.extract_text() or "")
    except ExtractError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ExtractError(f"PDF tidak dapat dibaca: {e}") from e
    text = "\n\n".join(pages)
    # gabungkan baris yang terputus di tengah kalimat (umum pada PDF)
    text = re.sub(r"(?<![.!?:;\n])\n(?!\n)(?=[a-z0-9(])", " ", text)
    text = re.sub(r"-\n(?=[a-z])", "", text)
    return text, len(pages)


def _docx(data: bytes) -> str:
    import docx

    try:
        d = docx.Document(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        raise ExtractError(f"DOCX tidak dapat dibaca: {e}") from e
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(dict.fromkeys(cells)))
    return "\n\n".join(parts)


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def extract(filename: str, data: bytes) -> dict:
    name = (filename or "").lower()
    pages = None
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        text, pages = _pdf(data)
    elif name.endswith(".docx"):
        text = _docx(data)
    elif name.endswith((".html", ".htm")):
        p = _HTMLText()
        p.feed(_decode(data))
        text = "".join(p.parts)
    elif name.endswith((".txt", ".md")) or not re.search(r"\.[a-z0-9]{2,5}$", name):
        text = _decode(data)
    elif name.endswith(".doc"):
        raise ExtractError("Format .doc lama belum didukung. Simpan ulang sebagai .docx atau PDF.")
    else:
        raise ExtractError(f"Format file tidak didukung. Gunakan: {', '.join(SUPPORTED)}")
    text = normalize_text(text)
    if not text:
        raise ExtractError("Tidak ada teks yang dapat diambil dari file ini (mungkin hasil scan/gambar).")
    return {"text": text, "pages": pages, "chars": len(text)}
