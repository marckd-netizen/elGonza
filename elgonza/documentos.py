"""Carga de la carpeta de un despacho: identifica cada documento y extrae su texto (con OCR si es escaneado).

Nunca escribe en la carpeta del despacho. El texto de OCR se guarda en una caché en la carpeta del usuario.
"""
import hashlib
import io
import json
import os
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

TIPOS = {
    "borrador": ["borrador"],
    "flete": ["flete", "freight", "carta de flete"],
    "origen": ["co", "certificado de origen", "origem", "origin"],
    "seguro": ["seguro", "seg", "cese", "insurance", "poliza"],
    "packing": ["pack", "empaque"],
    "gastos": ["gastos", "gtos", "thc", "local"],
    "transporte": ["conc", "bl", "awb", "guia", "crt", "mic", "hbl", "mbl", "conocimiento"],
    "factura": ["fact", "fac", "fatura", "inv", "factura", "invoice"],
}

# Si el nombre no alcanza, se mira el contenido
PISTAS_CONTENIDO = [
    ("origen", ["CERTIFICADO DE ORIGEN", "CERTIFICADO DE ORIGEM", "CERTIFICATE OF ORIGIN"]),
    ("transporte", ["BILL OF LADING", "AIR WAYBILL", "CONOCIMIENTO DE EMBARQUE", "CARTA DE PORTE", "WAYBILL"]),
    ("packing", ["PACKING LIST", "LISTA DE EMPAQUE"]),
    ("seguro", ["CERTIFICADO DE SEGURO", "INSURANCE CERTIFICATE", "POLIZA"]),
    ("flete", ["CONSTANCIA DE FLETE", "FREIGHT"]),
    ("factura", ["COMMERCIAL INVOICE", "FACTURA", "INVOICE"]),
]


@dataclass
class Documento:
    ruta: Path
    tipo: str
    texto: str = ""
    ocr: bool = False
    error: str = ""

    @property
    def nombre(self):
        return self.ruta.name


def _sin_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def tipo_por_nombre(nombre):
    n = _sin_tildes(Path(nombre).stem.lower())
    palabras = set(re.split(r"[^a-z]+", n))
    for tipo, claves in TIPOS.items():
        for c in claves:
            if " " in c and c in n:
                return tipo
            if c in palabras or any(p.startswith(c) and len(c) >= 4 for p in palabras):
                return tipo
    return "otro"


def tipo_por_contenido(texto):
    t = texto.upper()
    for tipo, pistas in PISTAS_CONTENIDO:
        if any(p in t for p in pistas):
            return tipo
    return "otro"


def carpeta_cache():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), ".cache")
    d = Path(base) / "elGonza" / "ocr"
    d.mkdir(parents=True, exist_ok=True)
    return d


def configurar_tesseract():
    """Busca tesseract.exe en las rutas habituales de Windows si no está en el PATH."""
    import pytesseract
    if shutil.which("tesseract"):
        return True
    candidatos = [
        os.environ.get("TESSERACT_CMD", ""),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR", "tesseract.exe"),
    ]
    if getattr(sys, "frozen", False):
        candidatos.insert(0, os.path.join(os.path.dirname(sys.executable), "Tesseract-OCR", "tesseract.exe"))
    for c in candidatos:
        if c and os.path.isfile(c):
            pytesseract.pytesseract.tesseract_cmd = c
            return True
    return False


def _idiomas_ocr():
    import pytesseract
    try:
        disponibles = set(pytesseract.get_languages(config=""))
    except Exception:
        return "eng"
    pedidos = [l for l in ("spa", "eng") if l in disponibles]
    return "+".join(pedidos) or "eng"


VERSION_TEXTO = 4


def extraer_texto(ruta, usar_ocr=True, dpi=300):
    """Devuelve (texto, fue_ocr). Usa el texto del PDF si lo tiene; si no, OCR por página."""
    try:
        import pymupdf as fitz
    except ImportError:  # versiones viejas de PyMuPDF
        import fitz
    ruta = Path(ruta)
    datos = ruta.read_bytes()
    clave = "v2-" + hashlib.sha1(datos).hexdigest()
    cache = carpeta_cache() / f"{clave}.json"
    if cache.exists():
        try:
            c = json.loads(cache.read_text(encoding="utf-8"))
            # el texto de PDF es barato de rehacer; el OCR solo se rehace si salió ruidoso con una versión vieja
            vigente = c.get("ver", 2) >= VERSION_TEXTO or (c.get("ocr") and not _ruidoso(c.get("texto", "")))
            if vigente and (c.get("ocr") or not usar_ocr or c.get("texto", "").strip()):
                return c["texto"], c["ocr"]
        except Exception:
            pass

    textos, fue_ocr = [], False
    if ruta.suffix.lower() == ".pdf":
        doc = fitz.open(stream=datos, filetype="pdf")
        paginas = list(doc)
    else:
        doc, paginas = None, [None]
    for pagina in paginas:
        t = pagina.get_text() if pagina is not None else ""
        if len(t.strip()) >= 40:
            t = _texto_con_columnas(datos, pagina.number) or t
        if len(t.strip()) < 40 and usar_ocr:
            t = _ocr_pagina(pagina, datos if pagina is None else None, dpi)
            fue_ocr = True
        textos.append(t)
    texto = "\n\f\n".join(textos)
    try:
        cache.write_text(json.dumps({"texto": texto, "ocr": fue_ocr, "ver": VERSION_TEXTO}), encoding="utf-8")
    except OSError:
        pass
    return texto, fue_ocr


def _texto_con_columnas(datos, nro_pagina):
    """Texto respetando la disposición (etiqueta y valor en la misma línea)."""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(datos)) as pdf:
            # dedupe_chars: algunos PDF imprimen cada letra dos veces para simular negrita ("FFOOBB")
            t = pdf.pages[nro_pagina].dedupe_chars().extract_text(layout=True) or ""
        return "\n".join(re.sub(r"[ \t]+$", "", l) for l in t.splitlines() if l.strip())
    except Exception:
        return ""


def _ocr_pagina(pagina, datos_imagen, dpi):
    import pytesseract
    from PIL import Image
    if pagina is not None:
        pix = pagina.get_pixmap(dpi=dpi)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
    else:
        img = Image.open(io.BytesIO(datos_imagen))
    img = img.convert("L")
    texto = pytesseract.image_to_string(img, lang=_idiomas_ocr(), config="--psm 6")
    if _ruidoso(texto):
        # escaneo con puntitos: se limpia y se lee por bloques sueltos (sirve para formularios con celdas)
        from PIL import ImageFilter
        limpia = img.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.MedianFilter(3))
        texto += "\n" + pytesseract.image_to_string(limpia, lang=_idiomas_ocr(), config="--psm 11")
    return texto


def _ruidoso(texto):
    """True si la mayoría de las palabras son basura de 1 o 2 letras (escaneo sucio)."""
    palabras = texto.split()
    if len(palabras) < 30:
        return False
    cortas = sum(1 for p in palabras if len(p) <= 2 and not p.isdigit())
    return cortas / len(palabras) > 0.35


EXTENSIONES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def cargar_carpeta(carpeta, usar_ocr=True, avisar=None):
    """Lista los documentos de la carpeta del despacho. Devuelve (ruta_borrador, [Documento])."""
    carpeta = Path(carpeta)
    archivos = sorted(p for p in carpeta.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONES)
    borrador = None
    docs = []
    if usar_ocr:
        usar_ocr = configurar_tesseract()
        if not usar_ocr and avisar:
            avisar("No encontré Tesseract OCR: solo se leerán los PDF que tengan texto.")
    for p in archivos:
        tipo = tipo_por_nombre(p.name)
        if tipo == "borrador":
            borrador = p
            continue
        if avisar:
            avisar(f"Leyendo {p.name}…")
        d = Documento(ruta=p, tipo=tipo)
        try:
            d.texto, d.ocr = extraer_texto(p, usar_ocr=usar_ocr)
        except Exception as e:  # un documento ilegible no frena la revisión
            d.error = str(e)
        if d.tipo == "otro" and d.texto:
            d.tipo = tipo_por_contenido(d.texto)
        docs.append(d)
    return borrador, docs
