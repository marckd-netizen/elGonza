"""Factura a planilla: paso previo al borrador.

Lee las facturas (y su packing list) de una carpeta y arma la planilla de ítems para cargar el DUA, con el NCM
sugerido a 10 dígitos. No usa el borrador ni toca la revisión.

Cómo se lee una factura: cada renglón de ítem es un renglón donde cantidad × precio da el importe (con las dos
lecturas posibles de números como 0.232 o 2,000). El código y la descripción salen del texto a la izquierda de
los números, más los renglones partidos justo arriba o abajo. Los pesos salen del packing list: el peso de cada
bulto se reparte por cantidad entre los renglones del bulto, se suma por código y se reparte entre los ítems de
la factura con ese código (mismo código, mismo material: el peso por unidad es el mismo).
"""
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from .ncm import sugerir
from .numeros import num_libre

NUMERO = re.compile(r"\d[\d.,]*\d|\d")
SALTEAR_ARCHIVO = re.compile(r"(?i)borrador|flete|freight|seguro|insurance|\bbl\b|awb|guia|conoc|crt|planilla")
TOTAL = re.compile(r"(?i)\bTO?TA?L\b|\bTOAL\b|\bSUB-?TOTAL\b|\bSUMA\b")
DEDUCCION = re.compile(r"(?i)deduct|discount|commission|descuento|bonificaci|rebate")
UNIDADES = [
    (re.compile(r"(?i)^(M2|M²|SQM|SQ\.?M|MTS2)$"), "METROS CUADRADOS"),
    (re.compile(r"(?i)^(KGS?|KILOS?|N\.?W\.?)$"), "KILOGRAMOS"),
    (re.compile(r"(?i)^(M|MTS?|METERS?|METROS?)$"), "METROS"),
    (re.compile(r"(?i)^(L|LTS?|LITERS?|LITROS?)$"), "LITROS"),
    (re.compile(r"(?i)^(PCS|PC|PZS|UNITS?|UNIDADES|UN|U|QTY|SETS?|EA|EACH|ROLLS?)$"), "UNIDADES"),
]


# --- palabras con su posición ------------------------------------------------
@dataclass
class Palabra:
    texto: str
    x0: float
    x1: float
    top: float

    @property
    def valores(self):
        """Lecturas posibles si es un número (0.232 puede ser 0,232 o 232)."""
        if not NUMERO.fullmatch(self.texto):
            return set()
        v = {num_libre(self.texto)}
        if re.fullmatch(r"\d{1,3}[.,]\d{3}", self.texto):
            v.add(float(self.texto.replace(",", ".")))
        return {x for x in v if x is not None}

    @property
    def es_numero(self):
        return bool(NUMERO.fullmatch(self.texto))


@dataclass
class Renglon:
    pagina: int
    top: float
    palabras: list

    @property
    def texto(self):
        return " ".join(p.texto for p in self.palabras)

    @property
    def y(self):
        return self.pagina * 10000 + self.top


def _renglones(palabras, pagina, tol=3):
    res = []
    for p in sorted(palabras, key=lambda p: (p.top, p.x0)):
        if res and abs(res[-1].top - p.top) <= tol:
            res[-1].palabras.append(p)
        else:
            res.append(Renglon(pagina, p.top, [p]))
    for r in res:
        r.palabras.sort(key=lambda p: p.x0)
    return res


def _palabras_pdf(pagina):
    return [Palabra(w["text"], w["x0"], w["x1"], w["top"])
            for w in pagina.dedupe_chars().extract_words(x_tolerance=1.5, y_tolerance=2)]


def _palabras_ocr(ruta, nro, imagen=None, dpi=300):
    """Palabras de una página escaneada, en puntos (1/72") como las del PDF."""
    import pytesseract
    from .documentos import _idiomas_ocr, configurar_tesseract
    configurar_tesseract()
    if imagen is None:
        import pymupdf as fitz
        from PIL import Image
        with fitz.open(ruta) as doc:
            pix = doc[nro].get_pixmap(dpi=dpi)
            imagen = Image.open(io.BytesIO(pix.tobytes("png")))
    d = pytesseract.image_to_data(imagen.convert("L"), lang=_idiomas_ocr(), config="--psm 6",
                                  output_type=pytesseract.Output.DICT)
    k = 72 / dpi
    return [Palabra(t, d["left"][i] * k, (d["left"][i] + d["width"][i]) * k, d["top"][i] * k)
            for i, t in enumerate(d["text"]) if t.strip()]


def paginas_de(ruta, usar_ocr=True):
    """[[Renglon]] por página. Usa el texto del PDF; si una página no tiene, OCR."""
    ruta = Path(ruta)
    paginas = []
    if ruta.suffix.lower() == ".pdf":
        import pdfplumber
        with pdfplumber.open(ruta) as pdf:
            for i, pg in enumerate(pdf.pages):
                pal = _palabras_pdf(pg)
                if len(pal) < 10 and usar_ocr:
                    pal = _palabras_ocr(ruta, i)
                paginas.append(_renglones(pal, i))
    elif usar_ocr:
        from PIL import Image, ImageSequence
        for i, img in enumerate(ImageSequence.Iterator(Image.open(ruta))):
            paginas.append(_renglones(_palabras_ocr(ruta, i, img.copy()), i))
    return paginas


# --- modelo ------------------------------------------------------------------
@dataclass
class Linea:
    nro: str
    codigo: str
    descripcion: str
    cantidad: float
    precio: float
    importe: float
    unidad: str = "UNIDADES"
    ancho_mm: float = None
    neto: float = None
    bruto: float = None
    ncm: str = ""
    descripcion_es: str = ""
    motivo: str = ""
    y: float = 0


@dataclass
class Factura:
    archivo: str
    numero: str = ""
    fecha: str = ""          # dd/mm/aaaa
    lineas: list = field(default_factory=list)
    observaciones: list = field(default_factory=list)
    neto_total: float = None
    bruto_total: float = None
    bultos: float = None


# --- encabezados -------------------------------------------------------------
MESES = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
MESES.update({"ENE": 1, "ABR": 4, "AGO": 8, "SET": 9, "DIC": 12})


def _fecha(texto):
    """Fecha de la factura en dd/mm/aaaa (busca después de DATE / FECHA)."""
    for m in re.finditer(r"(?i)\b(?:DATE|FECHA|DATED)\b\s*[:.]?\s*(.{0,30})", texto):
        f = _leer_fecha(m.group(1))
        if f:
            return f
    return ""


def _leer_fecha(s):
    s = s.strip().upper()
    m = re.match(r"(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})", s)
    if m:
        a, me, d = (int(x) for x in m.groups())
    else:
        m = re.match(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", s)
        if m:
            d, me, a = (int(x) for x in m.groups())
        else:
            m = re.match(r"(\d{1,2})[\s\-./]*([A-Z]{3})[A-Z]*[\s\-.,/]*(\d{2,4})", s) or \
                re.match(r"([A-Z]{3})[A-Z]*[\s\-.]*(\d{1,2})(?:ST|ND|RD|TH)?[\s,.\-]*(\d{2,4})", s)
            if not m:
                return ""
            g = m.groups()
            if g[0].isdigit():
                d, me, a = int(g[0]), MESES.get(g[1]), int(g[2])
            else:
                me, d, a = MESES.get(g[0]), int(g[1]), int(g[2])
            if not me:
                return ""
    if a < 100:
        a += 2000
    if not (1 <= me <= 12 and 1 <= d <= 31):
        return ""
    return f"{d:02d}/{me:02d}/{a}"


def _numero_factura(texto):
    m = re.search(r"(?i)\b(?:INVOICE|FACTURA|INV)\.?\s*(?:NO|N[º°O]|NUMBER|NRO|#)?\.?\s*[:#]?\s*"
                  r"(?=[A-Z0-9\-/]*\d)([A-Z0-9][A-Z0-9\-/]{2,})", texto)
    return m.group(1) if m else ""


# --- renglones de ítems ------------------------------------------------------
def _triple(nums):
    """(i_cant, i_precio, i_importe, cant, precio, importe) con cant × precio = importe; el importe a la derecha."""
    mejor = None
    for k in range(2, len(nums)):
        for t in nums[k].valores:
            for i in range(k):
                for j in range(i + 1, k):
                    for a in nums[i].valores:
                        for c in nums[j].valores:
                            if 1 in (a, c) or t in (a, c) or abs(a * c - t) > max(0.011, 0.0005 * t):
                                continue
                            # la cantidad va antes que el precio y suele ser la más grande
                            clave = (k, t, i + j)
                            if not mejor or clave > mejor[0]:
                                mejor = (clave, (i, j, k, a, c, t))
    return mejor[1] if mejor else None


def _es_codigo(trozo):
    """Un código de producto: una sola palabra, sin minúsculas, con número o guion (AL-MH, PP-Z(60H), S50#(TC))."""
    return " " not in trozo and not re.search(r"[a-z]", trozo) and bool(re.search(r"[\d\-]", trozo)) \
        and bool(re.search(r"[A-Z]", trozo + "-" if trozo.endswith("-") else trozo))


def _trozos(palabras, gap=6):
    res = []
    for p in palabras:
        if res and p.x0 - res[-1][-1].x1 <= gap:
            res[-1].append(p)
        else:
            res.append([p])
    return [" ".join(w.texto for w in t) for t in res]


def _codigo_y_descripcion(partes):
    """partes: listas de palabras (renglón partido arriba, renglón del ítem, abajo), en orden."""
    codigo, desc = [], []
    for palabras in partes:
        for n, t in enumerate(_trozos(palabras)):
            # el código va primero en su renglón (o sigue a un código cortado en «PP-»)
            if _es_codigo(t) and (n == 0 or (codigo and codigo[-1].endswith("-") and not desc)):
                codigo.append(t)
            else:
                desc.append(t)
    return "".join(codigo), re.sub(r"\s+", " ", " ".join(desc)).strip()


def _interlineado(tops):
    difs = sorted(b - a for a, b in zip(tops, tops[1:]) if b > a)
    return difs[len(difs) // 2] if difs else 20


def _unidad(encabezados, x0, x1):
    """Unidad de la columna de cantidad, por la palabra del encabezado que está encima."""
    mejor = None
    for r in encabezados:
        for p in r.palabras:
            for patron, nombre in UNIDADES:
                if patron.match(p.texto.strip("()")):
                    d = abs((p.x0 + p.x1) / 2 - (x0 + x1) / 2)
                    if d < 25 and (not mejor or d < mejor[0]):
                        mejor = (d, nombre)
    return mejor[1] if mejor else "UNIDADES"


def _columna(encabezados, patron):
    for r in encabezados:
        for p in r.palabras:
            if re.fullmatch(patron, p.texto, re.I):
                return p
    return None


def leer_items(renglones):
    """Líneas de una factura (renglones de sus páginas, en orden)."""
    filas = []
    for r in renglones:
        if TOTAL.search(r.texto) or DEDUCCION.search(r.texto):
            continue
        nums = [p for p in r.palabras if p.es_numero]
        t = _triple(nums[-12:]) if len(nums) >= 3 else None
        if t:
            filas.append((r, nums[-12:], t))
    if not filas:
        return []
    primera = filas[0][0]
    encabezados = [r for r in renglones if r.pagina == primera.pagina and primera.top - 60 <= r.top < primera.top]
    ancho_col = _columna(encabezados, r"WIDTH|ANCHO")
    alto = _interlineado([f[0].top for f in filas])
    usados = {id(f[0]) for f in filas}
    lineas = []
    for n, (r, nums, (i, j, k, cant, precio, importe)) in enumerate(filas, 1):
        # la parte de texto termina donde empieza la tira final de números del renglón
        pal = r.palabras
        corte = len(pal)
        while corte > 0 and pal[corte - 1].es_numero:
            corte -= 1
        nro = ""
        if corte == 0 or (pal[0].es_numero and re.fullmatch(r"\d{1,4}", pal[0].texto) and pal[0] is not nums[i]):
            nro, inicio = pal[0].texto, 1
        else:
            inicio = 0
        limite = (pal[corte].x0 if corte < len(pal) and corte > 0 else
                  next((p.x0 for p in pal[1:] if p.es_numero), pal[-1].x0)) - 2
        texto_fila = [p for p in pal[inicio:] if p.x1 <= limite and not (p.es_numero and p in nums[i:])]
        # renglones partidos: sin números, pegados arriba o abajo (más cerca de este ítem que de otro)
        arriba, abajo = [], []
        for o in renglones:
            if id(o) in usados or o.pagina != r.pagina or TOTAL.search(o.texto) or any(p.es_numero for p in o.palabras):
                continue
            d = o.top - r.top
            if 0 < abs(d) < alto * 0.6:
                otras = [abs(o.top - f[0].top) for f in filas if f[0] is not r and f[0].pagina == r.pagina]
                if otras and min(otras) < abs(d):
                    continue
                pal_o = [p for p in o.palabras if p.x1 <= limite]
                (arriba if d < 0 else abajo).append(pal_o)
        codigo, desc = _codigo_y_descripcion(arriba + [texto_fila] + abajo)
        ancho = None
        if ancho_col:
            for p in nums:
                if p.x0 <= ancho_col.x1 + 5 and p.x1 >= ancho_col.x0 - 5:
                    v = num_libre(p.texto)
                    ancho = v * 10 if v and _columna(encabezados, r"\(?CM\)?") else v
                    break
        lineas.append(Linea(nro or str(n), codigo, desc, cant, precio, round(importe, 2),
                            _unidad(encabezados, nums[i].x0, nums[i].x1), ancho, y=r.y))
    return lineas


# --- packing list ------------------------------------------------------------
def _norm(c):
    return re.sub(r"[^A-Z0-9]", "", (c or "").upper())


def pesos_packing(renglones, lineas, obs):
    """Reparte el neto y el bruto del packing entre las líneas. Devuelve (neto_total, bruto_total, bultos)."""
    if not renglones or not lineas:
        return None, None, None
    total_cant = round(sum(l.cantidad for l in lineas), 2)
    tot = None
    for r in renglones:
        if TOTAL.search(r.texto):
            nums = [p for p in r.palabras if p.es_numero]
            if any(abs(v - total_cant) <= max(0.02, total_cant * 0.0005) for p in nums for v in p.valores):
                tot = (r, nums)
    if not tot:
        obs.append("Packing list: no encontré el renglón de totales con la misma cantidad que la factura; "
                   "los pesos quedan sin repartir.")
        return None, None, None
    tr, tn = tot
    cols = {}
    i_cant = next(i for i, p in enumerate(tn) if any(abs(v - total_cant) <= max(0.02, total_cant * 0.0005) for v in p.valores))
    cols["cant"] = tn[i_cant]
    enc = [r for r in renglones if r.pagina == renglones[0].pagina and r.top < 330]
    for clave, patron in (("neto", r"N\.?W\.?|NET|NETO|N\.W\(KGS?\)"), ("bruto", r"G\.?W\.?|GROSS|BRUTO")):
        h = _columna(enc, patron)
        if h:
            c = min(tn[i_cant + 1:], key=lambda p: abs((p.x0 + p.x1) / 2 - (h.x0 + h.x1) / 2), default=None)
            if c:
                cols[clave] = c
    resto = tn[i_cant + 1:]
    if "neto" not in cols or "bruto" not in cols:
        # sin encabezado: los dos números del total con bruto >= neto más pegados (PCS, N.W., G.W., CBM)
        for a, b in zip(resto, resto[1:]):
            va, vb = num_libre(a.texto), num_libre(b.texto)
            if va and vb and vb >= va > 0 and vb - va < va * 0.3:
                cols["neto"], cols["bruto"] = a, b
                break
    if "neto" not in cols:
        obs.append("Packing list: no encontré las columnas de peso neto y bruto.")
        return None, None, None
    i_neto = tn.index(cols["neto"])
    bultos = num_libre(tn[i_neto - 1].texto) if i_neto - 1 > i_cant else None

    def en_col(p, c):
        return p.x0 <= c.x1 + 6 and p.x1 >= c.x0 - 6

    filas, marcas = [], []
    for r in renglones:
        if r is tr or TOTAL.search(r.texto):
            continue
        nums = [p for p in r.palabras if p.es_numero]
        q = next((p for p in nums if en_col(p, cols["cant"])), None)
        n = next((p for p in nums if en_col(p, cols["neto"])), None)
        b = next((p for p in nums if "bruto" in cols and en_col(p, cols["bruto"])), None)
        if q:
            filas.append([r, num_libre(q.texto) if num_libre(q.texto) is not None else 0, q.x0])
        if n and (len(nums) <= 5 or q):
            marcas.append((r.y, num_libre(n.texto) or 0, num_libre(b.texto) if b else None))
    if not filas or not marcas:
        obs.append("Packing list: no pude leer los renglones de pesos.")
        return None, None, None
    # código de cada renglón del packing, con sus renglones partidos
    alto = _interlineado(sorted(f[0].top for f in filas))
    pack = []
    for r, q, x_cant in filas:
        partes = []
        for o in renglones:
            if o.pagina == r.pagina and o is not r and 0 < abs(o.top - r.top) < alto * 0.6 \
                    and not any(p.es_numero for p in o.palabras):
                otras = [abs(o.top - f[0].top) for f in filas if f[0] is not r and f[0].pagina == r.pagina]
                if not otras or min(otras) >= abs(o.top - r.top):
                    partes.append((o.top, [p for p in o.palabras if p.x1 < x_cant - 60]))
        pal = r.palabras
        if pal and pal[0].es_numero and re.fullmatch(r"\d{1,4}", pal[0].texto):
            pal = pal[1:]
        partes.append((r.top, [p for p in pal if not p.es_numero and p.x1 < x_cant - 60]))
        codigo, _ = _codigo_y_descripcion([p for _, p in sorted(partes, key=lambda t: t[0])])
        pack.append((r.y, codigo, q))
    grupos = _agrupar([p[0] for p in pack], [m[0] for m in marcas], alto)
    if grupos is None:
        obs.append("Packing list: no pude ubicar cada renglón en su bulto; los pesos quedan sin repartir.")
        return None, None, None
    por_codigo = {}
    for g, (_, neto, bruto) in zip(grupos, marcas):
        cant_g = sum(pack[i][2] for i in g) or 1
        for i in g:
            c = _norm(pack[i][1])
            acc = por_codigo.setdefault(c, [0.0, 0.0, 0.0])
            acc[0] += neto * pack[i][2] / cant_g
            acc[1] += (bruto or 0) * pack[i][2] / cant_g
            acc[2] += pack[i][2]
    cant_fact = {}
    for l in lineas:
        cant_fact[_norm(l.codigo)] = cant_fact.get(_norm(l.codigo), 0) + l.cantidad
    for c, q in cant_fact.items():
        if c not in por_codigo:
            obs.append(f"El código {c or '(sin código)'} de la factura no aparece en el packing list: sin pesos.")
        elif abs(por_codigo[c][2] - q) > max(0.02, q * 0.0005):
            obs.append(f"Código {c}: la factura suma {q:,.2f} y el packing list {por_codigo[c][2]:,.2f}.")
    for c in por_codigo:
        if c not in cant_fact:
            obs.append(f"El código {c} del packing list no está en la factura.")
    for l in lineas:
        c = _norm(l.codigo)
        if c in por_codigo and cant_fact.get(c):
            f = l.cantidad / cant_fact[c]
            l.neto = round(por_codigo[c][0] * f, 2)
            l.bruto = round(por_codigo[c][1] * f, 2) if any(m[2] for m in marcas) else None
    neto_t = num_libre(cols["neto"].texto)
    bruto_t = num_libre(cols["bruto"].texto) if "bruto" in cols else None
    suma_n = sum(m[1] for m in marcas)
    if neto_t and abs(suma_n - neto_t) > 1:
        obs.append(f"Packing list: los netos por bulto suman {suma_n:,.2f} y el total dice {neto_t:,.2f}.")
    return neto_t, bruto_t, bultos


def _agrupar(filas_y, marcas_y, alto):
    """Parte las filas (en orden) en tantos grupos contiguos como marcas de peso, con cada marca dentro de su
    grupo y lo más centrada posible. Devuelve [[índices]] o None."""
    n, g = len(filas_y), len(marcas_y)
    if g > n:
        return None
    inf = float("inf")
    dp = [[inf] * (n + 1) for _ in range(g + 1)]
    desde = [[0] * (n + 1) for _ in range(g + 1)]
    dp[0][0] = 0
    for k in range(1, g + 1):
        m = marcas_y[k - 1]
        for i in range(k, n + 1):
            for j in range(k - 1, i):
                if dp[k - 1][j] == inf:
                    continue
                ys = filas_y[j:i]
                if not (ys[0] - alto <= m <= ys[-1] + alto):
                    continue
                costo = dp[k - 1][j] + (sum(ys) / len(ys) - m) ** 2
                if costo < dp[k][i]:
                    dp[k][i], desde[k][i] = costo, j
    if dp[g][n] == inf:
        return None
    grupos, i = [], n
    for k in range(g, 0, -1):
        j = desde[k][i]
        grupos.append(list(range(j, i)))
        i = j
    return grupos[::-1]


# --- carpeta -----------------------------------------------------------------
def _tipo_pagina(renglones, anterior):
    t = " ".join(r.texto for r in renglones[:40]).upper()
    if re.search(r"PACKING|PACKAGE LIST|LISTA DE EMPAQUE|EMPAQUE", t):
        return "packing"
    if re.search(r"INVOICE|FACTURA", t):
        return "factura"
    return anterior


def leer_carpeta(carpeta, usar_ocr=True, avisar=None):
    """Facturas de la carpeta (en orden de archivo), cada una con sus líneas, pesos y observaciones."""
    carpeta = Path(carpeta)
    archivos = sorted(p for p in carpeta.iterdir() if p.is_file()
                      and p.suffix.lower() in (".pdf", ".tif", ".tiff", ".jpg", ".jpeg", ".png")
                      and not SALTEAR_ARCHIVO.search(p.stem))
    facturas, packings = [], []   # packings: (texto, renglones)
    for a in archivos:
        if avisar:
            avisar(f"Leyendo {a.name}…")
        tipo, bloques = None, []
        for renglones in paginas_de(a, usar_ocr):
            nuevo = _tipo_pagina(renglones, tipo)
            if nuevo == "factura" and tipo == "factura" and bloques and _inicia_factura(renglones, bloques[-1][1]):
                bloques.append(("factura", []))
            elif nuevo != tipo or not bloques:
                bloques.append((nuevo, []))
            tipo = nuevo
            bloques[-1][1].extend(renglones)
        if not tipo and re.search(r"(?i)pack|pkl", a.stem):
            bloques = [("packing", r) for _, r in bloques]
        for t, rs in bloques:
            texto = "\n".join(r.texto for r in rs)
            if t == "packing":
                packings.append((texto, rs))
                continue
            lineas = leer_items(rs)
            if not lineas:
                continue
            f = Factura(a.name, _numero_factura(texto), _fecha(texto), lineas)
            f.texto = texto
            controles_factura(f, rs)
            facturas.append(f)
    for f in facturas:
        propios = [rs for texto, rs in packings if f.numero and _norm(f.numero) in _norm(texto)]
        if not propios and len(facturas) == 1:
            propios = [rs for _, rs in packings]
        if propios:
            f.neto_total, f.bruto_total, f.bultos = pesos_packing([r for rs in propios for r in rs], f.lineas,
                                                                   f.observaciones)
        else:
            f.observaciones.append("No encontré el packing list de esta factura: neto y bruto quedan vacíos.")
        for l in f.lineas:
            l.ncm, l.descripcion_es, l.motivo = sugerir(l.codigo, l.descripcion, l.ancho_mm)
    return facturas


def _inicia_factura(renglones, previos):
    """Una página de factura que repite el encabezado con otro número es otra factura."""
    a = _numero_factura("\n".join(r.texto for r in renglones[:30]))
    b = _numero_factura("\n".join(r.texto for r in previos[:30]))
    return bool(a and b and a != b)


def controles_factura(f, renglones):
    """Observaciones sobre los totales de la factura."""
    suma = round(sum(l.importe for l in f.lineas), 2)
    cant = round(sum(l.cantidad for l in f.lineas), 2)
    vistos = False
    for r in renglones:
        if DEDUCCION.search(r.texto):
            f.observaciones.append(f"La factura tiene un descuento o deducción: «{r.texto.strip()}». "
                                   "Revisar si corresponde a esta operación antes de restarlo del valor.")
        if not TOTAL.search(r.texto):
            continue
        vals = [v for p in r.palabras if p.es_numero for v in p.valores]
        if any(abs(v - suma) <= 0.02 for v in vals):
            vistos = True
        for v in vals:
            if cant and abs(v - 2 * cant) <= 0.02:
                f.observaciones.append(f"Un renglón de total dice {v:,.2f}, el doble de la cantidad real "
                                       f"({cant:,.2f}): «{r.texto.strip()}».")
    if not vistos:
        f.observaciones.append(f"No encontré en la factura un total igual a la suma de los ítems ({suma:,.2f}).")


# --- planilla ------------------------------------------------------------------
COLUMNAS = ["CANTIDAD", "VALOR", "NCM", "DESCRIPCION", "NETO", "BRUTO", "UNIDAD MEDIDA", "CODIGO FACTURA",
            "DESCRIPCION FACTURA", "NCM SUGERIDO"]


def generar_planilla(facturas, ruta):
    """Una sola factura: ITEM y las columnas del modelo. Varias: ITEM (correlativo), FACTURA y FECHA delante."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from .ncm import arancel

    varias = len(facturas) > 1
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Hoja1"
    ws.append(["ITEM"] + (["FACTURA", "FECHA"] if varias else []) + COLUMNAS)
    n = 0
    for f in facturas:
        for l in f.lineas:
            n += 1
            ws.append([n if varias else (int(l.nro) if l.nro.isdigit() else n)] + ([f.numero, f.fecha] if varias else [])
                      + [l.cantidad, l.importe, None, l.descripcion_es or None, l.neto, l.bruto, l.unidad, l.codigo,
                         l.descripcion, l.ncm or None])
    amarillo = PatternFill("solid", fgColor="FFF2CC")
    for c in ws[1]:
        c.font = Font(bold=True)
    ultima = ws.max_column
    for fila in ws.iter_rows(min_row=1):
        fila[ultima - 1].fill = amarillo
    d = 3 if varias else 1
    anchos = [6] + ([16, 11] if varias else []) + [11, 11, 14, 55, 10, 10, 20, 20, 50, 15]
    for i, a in enumerate(anchos):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i + 1)].width = a
    for fila in ws.iter_rows(min_row=2):
        for k in (0, 1, 4, 5):
            fila[d + k].number_format = "#,##0.00"
    ws.freeze_panes = "A2"

    ns = wb.create_sheet("Notas")
    ns.column_dimensions["A"].width = 45
    ns.column_dimensions["B"].width = 100
    tabla = arancel()
    for f in facturas:
        ns.append([f"FACTURA {f.numero or '(sin número)'} · {f.fecha or 'sin fecha'} · {f.archivo}"])
        ns.cell(ns.max_row, 1).font = Font(bold=True)
        ns.append(["Ítems", len(f.lineas)])
        ns.append(["Cantidad total", round(sum(l.cantidad for l in f.lineas), 2)])
        ns.append(["Valor total (suma de ítems)", round(sum(l.importe for l in f.lineas), 2)])
        if f.neto_total:
            ns.append(["Neto / bruto (packing list)", f.neto_total, f.bruto_total])
        if f.bultos:
            ns.append(["Bultos", f.bultos])
        for o in f.observaciones:
            ns.append(["Observación", o])
        ns.append([])
    ns.append(["NCM SUGERIDO"])
    ns.cell(ns.max_row, 1).font = Font(bold=True)
    vistos = set()
    for f in facturas:
        for l in f.lineas:
            clave = (l.codigo, l.ncm, l.motivo)
            if clave in vistos:
                continue
            vistos.add(clave)
            info = tabla.get(l.ncm)
            tasas = f" (AEC {info.aec or '-'}, E/Z {info.ez or '-'}, I/Z {info.iz or '-'})" if info else ""
            ns.append([f"{l.codigo or l.descripcion[:30]} → {l.ncm or 'sin sugerencia'}{tasas}", l.motivo])
    ns.append([])
    ns.append(["NETO y BRUTO: el packing list da el peso por bulto; se reparte por cantidad dentro de cada bulto y "
               "por código entre los ítems de la factura."])
    ns.append(["La columna NCM SUGERIDO es una sugerencia: hay que confirmarla antes de cargar el DUA."])
    ruta = Path(ruta)
    wb.save(ruta)
    return ruta


def planilla_de_carpeta(carpeta, salida=None, usar_ocr=True, avisar=print):
    """Lee la carpeta y guarda la planilla. Devuelve (ruta, facturas) o (None, mensaje)."""
    carpeta = Path(carpeta)
    facturas = leer_carpeta(carpeta, usar_ocr=usar_ocr, avisar=avisar)
    if not facturas:
        return None, f"No encontré facturas con ítems en {carpeta}"
    ruta = Path(salida) if salida else carpeta / f"Planilla {carpeta.name}.xlsx"
    return generar_planilla(facturas, ruta), facturas
