"""Documentos de una carpeta tomados de Mega6 (solo lectura).

Mega6 guarda las imágenes de los documentos RADE en <Permiso6>\\IMGRADE\\NNNN\\M00xxxxxx.tif, en carpetas de a
mil. El archivo A2137081 (y su par A2137080) une cada documento con su carpeta, su número RADE y su imagen:

    I145475FACNE-0043-00001897 ... A00144715891124784686
    │ │     │   │                  │        └ número RADE
    │ │     │   └ número del doc.  └ número de imagen
    │ │     └ tipo (FACN, CONC, FACC, PACK…)
    │ └ carpeta
    └ importación / exportación

Los archivos son RM/COBOL indexados con compresión de espacios y ceros. Nunca se escriben: se leen enteros y el
índice que sale se guarda en la caché del usuario.
"""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

ARCHIVOS_INDICE = ("A2137081", "A2137080")
CANDIDATOS = (r"F:\Permiso6", r"D:\Permiso6", r"C:\Permiso6")

# tipo de documento de Mega6 -> tipo de elGonza (los que no están se deciden por el contenido)
TIPOS = {
    "FACN": "factura",
    "FAFL": "flete", "FACF": "flete", "PRFL": "flete",
    "PACK": "packing",
    "CONC": "transporte",
    "CHIL": "origen", "ALAD": "origen", "ORIM": "origen", "COLO": "origen", "ISRA": "origen",
    "MEXI": "origen", "EURO": "origen", "MITU": "origen",
}
# FACC es «factura de flete u otros gastos»: si el contenido parece una factura cualquiera, es flete
FACTURA_ES_FLETE = {"FACC"}
# documentos que no aportan a la revisión y no vale la pena pasar por OCR
OMITIR = {"AURI"}


@dataclass
class DocMega6:
    carpeta: str
    codigo: str      # FACN, CONC…
    numero: str      # número del documento
    rade: str
    imagen: int

    @property
    def tipo(self):
        return TIPOS.get(self.codigo, "otro")


def raiz_mega6():
    """Carpeta Permiso6 de Mega6: la variable ELGONZA_MEGA6 o la primera de las rutas habituales que exista."""
    for c in (os.environ.get("ELGONZA_MEGA6", ""),) + CANDIDATOS:
        if c and (Path(c) / ARCHIVOS_INDICE[0]).is_file():
            return Path(c)
    return None


def _dec(seg):
    """Descomprime un registro RM/COBOL. None si los bytes no forman un registro válido."""
    out = []
    i, n = 0, len(seg)
    while i < n:
        c = seg[i]
        i += 1
        if c < 0x80:
            if i + c > n:
                return None
            out.append(seg[i:i + c].decode("latin-1"))
            i += c
        elif c < 0xC0:
            out.append(" " * (c - 0x7E))
        elif c < 0xE0:
            out.append("0" * (c - 0xBE))
        else:
            if i >= n:
                return None
            out.append(chr(seg[i]) * (c - 0xE5))
            i += 1
    return "".join(out)


INICIO = re.compile(rb"[\x05-\x40][IE] ?\d{6}[A-Z]{4}")
REGISTRO = re.compile(r"[IE] ?(\d{6})([A-Z0-9]{4})(.{0,30}?)(?:\s{2,}|\d\d[IE]A?\s?\d{6})")
IMAGEN = re.compile(r"A(\d{8})(89\d{10})")


def leer_indice(datos):
    """Recorre los bytes de A2137081 y devuelve {rade: DocMega6}."""
    docs = {}
    for m in INICIO.finditer(datos):
        q = m.start()
        largo = int.from_bytes(datos[q - 2:q], "big")
        if not 20 <= largo <= 600:
            continue
        d = _dec(datos[q:q + largo])
        if not d:
            continue
        r, im = REGISTRO.match(d), IMAGEN.search(d)
        if r and im:
            docs[im.group(2)] = DocMega6(r.group(1), r.group(2), r.group(3).strip(), im.group(2), int(im.group(1)))
    return docs


def indice(raiz=None):
    """{carpeta: [DocMega6]} de toda la base, con caché mientras los archivos de Mega6 no cambien."""
    from .documentos import carpeta_cache
    raiz = Path(raiz) if raiz else raiz_mega6()
    if not raiz:
        return {}
    archivos = [raiz / a for a in ARCHIVOS_INDICE if (raiz / a).is_file()]
    firma = [[a.name, a.stat().st_size, int(a.stat().st_mtime)] for a in archivos]
    cache = carpeta_cache().parent / "mega6-indice.json"
    docs = None
    try:
        c = json.loads(cache.read_text(encoding="utf-8"))
        if c.get("firma") == firma and c.get("raiz") == str(raiz):
            docs = [DocMega6(*x) for x in c["docs"]]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if docs is None:
        todos = {}
        for a in archivos:
            for rade, doc in leer_indice(a.read_bytes()).items():
                todos.setdefault(rade, doc)
        docs = list(todos.values())
        try:
            cache.write_text(json.dumps({"raiz": str(raiz), "firma": firma,
                                         "docs": [[d.carpeta, d.codigo, d.numero, d.rade, d.imagen] for d in docs]}),
                             encoding="utf-8")
        except OSError:
            pass
    por_carpeta = {}
    for d in docs:
        por_carpeta.setdefault(d.carpeta, []).append(d)
    return por_carpeta


def ruta_imagen(raiz, imagen):
    """IMGRADE\\0145\\M00144715.tif (o .pdf). None si no está en el disco."""
    base = Path(raiz) / "IMGRADE" / f"{imagen // 1000 + 1:04d}"
    for ext in (".tif", ".TIF", ".pdf", ".PDF", ".tiff"):
        p = base / f"M{imagen:08d}{ext}"
        if p.is_file():
            return p
    return None


def documentos_de_carpeta(carpeta, raiz=None):
    """[(DocMega6, ruta de la imagen)] de la carpeta, sin los que no sirven para la revisión."""
    raiz = Path(raiz) if raiz else raiz_mega6()
    if not raiz:
        return []
    carpeta = str(carpeta).strip().zfill(6)
    res = []
    for d in sorted(indice(raiz).get(carpeta, []), key=lambda d: d.imagen):
        if d.codigo in OMITIR:
            continue
        ruta = ruta_imagen(raiz, d.imagen)
        if ruta:
            res.append((d, ruta))
    return res


def borrador_de_carpeta(carpeta, raiz=None):
    """El PDF que Mega6 deja al generar el borrador: <Permiso6>\\respaldo\\Borrador149461.pdf."""
    raiz = Path(raiz) if raiz else raiz_mega6()
    if not raiz:
        return None
    p = raiz / "respaldo" / f"Borrador{str(carpeta).strip()}.pdf"
    return p if p.is_file() else None


def ultimo_borrador(raiz=None):
    """El borrador generado más recientemente en Mega6, o None."""
    raiz = Path(raiz) if raiz else raiz_mega6()
    carpeta = raiz / "respaldo" if raiz else None
    if not carpeta or not carpeta.is_dir():
        return None
    pdfs = [p for p in carpeta.glob("Borrador*.pdf") if p.is_file()]
    return max(pdfs, key=lambda p: p.stat().st_mtime, default=None)
