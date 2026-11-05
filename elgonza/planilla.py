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
MESES_LARGOS = dict(MESES, FEV=2, MAI=5, OUT=10, DEZ=12)


def _fecha(texto):
    """Fecha de la factura en dd/mm/aaaa (busca después de DATE / FECHA)."""
    for m in re.finditer(r"(?i)\b(?:DATE|FECHA|DATED)\b\s*[:.]?\s*(.{0,30})", texto):
        f = _leer_fecha(m.group(1))
        if f:
            return f
    # «BOM PRINCIPIO, 27 DE OCTUBRE DE 2026»
    m = re.search(r"(?i)\b(\d{1,2})\s+DE\s+([A-ZÁÉÍÓÚÇ]+)\s+DE\s+(\d{4})", texto)
    if m and MESES_LARGOS.get(m.group(2).upper()[:3]):
        return f"{int(m.group(1)):02d}/{MESES_LARGOS[m.group(2).upper()[:3]]:02d}/{m.group(3)}"
    m = re.search(r"\b(\d{1,2}/\d{1,2}/\d{4})\b", texto)
    return _leer_fecha(m.group(1)) if m else ""


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
    m = re.search(r"(?i)\b(?:INVOICE|FACTURA|FATURA|INV)\.?\s*(?:PROFORMA\s*|COMERCIAL\s*)?(?:NO|N[º°O]|NUMBER|NRO|#)?\.?\s*[:#]?\s*"
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


def _par_con_cantidad(nums, vecinos):
    """Factura con la cantidad en un renglón aparte (arriba, sola: «60 (Sesenta)»): busca en el renglón precio e
    importe y en los vecinos la cantidad que los une. Devuelve (q_palabra, i_precio, i_importe, cant, precio, importe)."""
    for o in vecinos:
        qs = [p for p in o.palabras if p.es_numero]
        if not qs or len(qs) > 2:
            continue
        for q in qs:
            for k in range(len(nums) - 1, 0, -1):
                for j in range(k):
                    if q.x1 >= nums[j].x0:
                        continue
                    for t in nums[k].valores:
                        for c in nums[j].valores:
                            for a in q.valores:
                                if 1 not in (a, c) and t not in (a, c) and abs(a * c - t) <= max(0.011, 0.0005 * t):
                                    return q, o, j, k, a, c, t
    return None


def _alineadas(filas_nums, tol=4):
    """Posiciones (x0, x1, centro) que se repiten en al menos la mitad de los renglones: columnas de números."""
    def marcas(p):
        return (round(p.x0), round(p.x1), round((p.x0 + p.x1) / 2))
    todas = [marcas(p) for nums in filas_nums for p in nums]
    minimo = max(2, len(filas_nums) // 2) if len(filas_nums) > 1 else 1

    def es_columna(p):
        m = marcas(p)
        return sum(1 for o in todas if any(abs(m[i] - o[i]) <= tol for i in range(3))) >= minimo
    return es_columna


MONEDA = re.compile(r"(?i)US?\$|\$|USD|EUR|€|R\$|U\$S")
UNIDAD_INICIAL = re.compile(r"^(UN|UND|UNID|PCS|PC|PZS|KGS?|M2|MTS?|SETS?|ROLLS?)\s+(?=\S)")
NCM_EN_TEXTO = re.compile(r"(?i)\bNCM\b\s*[:.]?\s*(\d{4}\.?\d{2}\.?\d{2}(?:\.?\d{2})?)")
CODIGO_EN_TEXTO = re.compile(r"-\s*([A-Z]*\d{5,}[A-Z0-9]*)\b")


def leer_items(renglones):
    """Líneas de una factura (renglones de sus páginas, en orden)."""
    filas, qlineas = [], set()
    for n, r in enumerate(renglones):
        if TOTAL.search(r.texto) or DEDUCCION.search(r.texto):
            continue
        nums = [p for p in r.palabras if p.es_numero][-12:]
        t = _triple(nums) if len(nums) >= 3 else None
        if t:
            i, j, k, a, c, tt = t
            filas.append((r, nums, nums[i], j, k, a, c, tt))
            continue
        if len(nums) >= 2 and id(r) not in qlineas:
            vecinos = [o for o in renglones[max(0, n - 2):n][::-1] + renglones[n + 1:n + 2]
                       if o.pagina == r.pagina and abs(o.top - r.top) < 25 and id(o) not in qlineas
                       and not TOTAL.search(o.texto)]
            par = _par_con_cantidad(nums, vecinos)
            if par:
                q, o, j, k, a, c, tt = par
                qlineas.add(id(o))
                filas.append((r, nums, q, j, k, a, c, tt))
    filas = [f for f in filas if id(f[0]) not in qlineas]
    if not filas:
        return []
    primera = filas[0][0]
    encabezados = [r for r in renglones if r.pagina == primera.pagina and primera.top - 60 <= r.top < primera.top]
    ancho_col = _columna(encabezados, r"WIDTH|ANCHO")
    alto = _interlineado([f[0].top for f in filas])
    renglon = {}
    for pg in {r.pagina for r in renglones}:
        renglon[pg] = _interlineado([r.top for r in renglones if r.pagina == pg])
    usados = {id(f[0]) for f in filas} | qlineas
    es_columna = _alineadas([f[1] for f in filas])
    lineas = []
    for n, (r, nums, q, j, k, cant, precio, importe) in enumerate(filas, 1):
        pal = r.palabras
        # el texto termina donde empiezan las columnas de números (las que se alinean entre renglones)
        corte = len(pal)
        while corte > 0 and (MONEDA.fullmatch(pal[corte - 1].texto) or pal[corte - 1].es_numero and (
                es_columna(pal[corte - 1]) or pal[corte - 1] in (q, nums[j], nums[k]))):
            corte -= 1
        nro, inicio = "", 0
        if pal and pal[0].es_numero and re.fullmatch(r"\d{1,4}", pal[0].texto) and pal[0] is not q \
                and (corte > 1 or corte == 0):
            nro, inicio = pal[0].texto, 1
            corte = max(corte, 1)
        limite = (pal[corte].x0 if corte < len(pal) else pal[-1].x1 + 1) - 2
        texto_fila = [p for p in pal[inicio:corte]]
        # renglones partidos: sin números en las columnas, pegados arriba o abajo (más cerca de este ítem que de otro)
        arriba, abajo = [], []
        for o in renglones:
            if id(o) in usados or o.pagina != r.pagina or TOTAL.search(o.texto):
                continue
            d = o.top - r.top
            if not (0 < abs(d) < max(alto * 0.6, renglon[r.pagina] * 1.15)) \
                    or any(p.es_numero and p.x1 > limite for p in o.palabras):
                continue
            otras = [abs(o.top - f[0].top) for f in filas if f[0] is not r and f[0].pagina == r.pagina]
            if otras and min(otras) < abs(d):
                continue
            (arriba if d < 0 else abajo).append((o, [p for p in o.palabras if p.x1 <= limite]))
        # un título de grupo («CHECK VALVE») no es parte del ítem: se toma lo de arriba solo si el ítem también
        # sigue abajo o si lo de arriba está pegado a otro renglón
        if arriba and not abajo:
            arriba = [(o, p) for o, p in arriba if any(0 < o.top - x.top <= renglon[o.pagina] * 1.5
                                                       for x in renglones if x.pagina == o.pagina
                                                    and id(x) not in qlineas)]
        # abajo, igual: un título del grupo siguiente queda más pegado a lo que tiene debajo que a este ítem
        abajo = [(o, p) for o, p in abajo if not any(0 < x.top - o.top <= o.top - r.top
                                                    for x in renglones if x.pagina == o.pagina
                                                    and id(x) not in qlineas)]
        arriba, abajo = [p for _, p in arriba], [p for _, p in abajo]
        codigo, desc = _codigo_y_descripcion(arriba + [texto_fila] + abajo)
        unidad = _unidad(encabezados, q.x0, q.x1) if q in nums else None
        m = UNIDAD_INICIAL.match(desc)
        if m:
            desc = desc[m.end():]
            unidad = unidad or next(nombre for patron, nombre in UNIDADES if patron.match(m.group(1)))
        if not codigo:
            mc = CODIGO_EN_TEXTO.search(desc)
            codigo = mc.group(1) if mc else ""
        ancho = None
        if ancho_col:
            for p in nums:
                if p.x0 <= ancho_col.x1 + 5 and p.x1 >= ancho_col.x0 - 5:
                    v = num_libre(p.texto)
                    ancho = v * 10 if v and _columna(encabezados, r"\(?CM\)?") else v
                    break
        mn = NCM_EN_TEXTO.search(desc)
        l = Linea(nro or str(n), codigo, desc, cant, precio, round(importe, 2), unidad or "UNIDADES", ancho, y=r.y)
        l.ncm_factura = re.sub(r"\D", "", mn.group(1)) if mn else ""
        lineas.append(l)
    return lineas


# --- packing list ------------------------------------------------------------
def _norm(c):
    return re.sub(r"[^A-Z0-9]", "", (c or "").upper())


def _decimal(texto):
    """Separador decimal del documento: '.' si hay números como 25,472.136; ',' si hay 25.472,136."""
    if re.search(r"\d,\d{3}\.\d", texto):
        return "."
    if re.search(r"\d\.\d{3},\d", texto):
        return ","
    return None


def _valor(tok, dec):
    t = (tok or "").replace(" ", "")
    if dec == ".":
        t = t.replace(",", "")
    elif dec == ",":
        t = t.replace(".", "").replace(",", ".")
    else:
        return num_libre(tok)
    try:
        return float(t)
    except ValueError:
        return None


NETO_ENC = r"N\.?W\.?|NETO|NET|NETWEIGHT"
BRUTO_ENC = r"G\.?W\.?|BRUTO|GROSS|GROSSWEIGHT"


def _total_en_texto(patron, texto, dec):
    m = re.search(patron + r"[^\d\n]{0,20}(\d[\d.,]*)", texto)
    return _valor(m.group(1), dec) if m else None


def pesos_por_item(renglones, lineas, obs):
    """Packing list con un renglón por producto y sus columnas de neto y bruto (sin bultos mezclados).
    Devuelve (neto, bruto, bultos) o None si el packing no tiene esa forma."""
    texto = "\n".join(r.texto for r in renglones)
    dec = _decimal(texto)
    col, entradas = None, []
    for i, r in enumerate(renglones):
        n = next((p for p in r.palabras if re.fullmatch(NETO_ENC, p.texto, re.I)), None)
        b = next((p for p in r.palabras if re.fullmatch(BRUTO_ENC, p.texto, re.I)), None)
        if n and b:
            col = (n, b)
            continue
        if not col or TOTAL.search(r.texto):
            continue
        cn, cb = ((p.x0 + p.x1) / 2 for p in col)
        nums = [p for p in r.palabras if p.es_numero]
        vn = min(nums, key=lambda p: abs((p.x0 + p.x1) / 2 - cn), default=None)
        vb = min(nums, key=lambda p: abs((p.x0 + p.x1) / 2 - cb), default=None)
        if not vn or not vb or vn is vb or abs((vn.x0 + vn.x1) / 2 - cn) > 25 or abs((vb.x0 + vb.x1) / 2 - cb) > 25:
            continue
        sig = renglones[i + 1] if i + 1 < len(renglones) and renglones[i + 1].pagina == r.pagina \
            and renglones[i + 1].top - r.top < 15 else None
        pal = r.palabras[1:] if r.palabras[0].es_numero and re.fullmatch(r"\d{1,4}", r.palabras[0].texto) else r.palabras
        pal = [p for p in pal if p.x1 < min(vn.x0, vb.x0) - 2]
        trozos = _trozos(pal)
        codigo = trozos[0] if trozos and _es_codigo(trozos[0]) else ""
        if not codigo:
            junto = UNIDAD_INICIAL.sub("", " ".join(t for t in trozos) + " " + (sig.texto if sig else ""))
            m = CODIGO_EN_TEXTO.search(junto)
            codigo = m.group(1) if m else ""
        entradas.append((_norm(codigo), _valor(vn.texto, dec), _valor(vb.texto, dec)))
    if not entradas:
        return None
    codigos = {_norm(l.codigo) for l in lineas}
    if not all(c and c in codigos for c, _, _ in entradas):
        if len(entradas) != len(lineas):
            return None   # renglones de peso sin producto: pesos por bulto
        entradas = [(_norm(l.codigo) or f"#{k}", n, b) for k, (l, (_, n, b)) in enumerate(zip(lineas, entradas))]
        for k, l in enumerate(lineas):
            l._clave = _norm(l.codigo) or f"#{k}"
    por_codigo = {}
    for c, n, b in entradas:
        acc = por_codigo.setdefault(c, [0.0, 0.0])
        acc[0] += n or 0
        acc[1] += b or 0
    cant = {}
    for k, l in enumerate(lineas):
        c = getattr(l, "_clave", _norm(l.codigo))
        cant[c] = cant.get(c, 0) + l.cantidad
    for k, l in enumerate(lineas):
        c = getattr(l, "_clave", _norm(l.codigo))
        if c in por_codigo:
            l.neto = round(por_codigo[c][0] * l.cantidad / cant[c], 3)
            l.bruto = round(por_codigo[c][1] * l.cantidad / cant[c], 3)
        else:
            obs.append(f"El producto {l.codigo or l.descripcion[:40]} no aparece en el packing list: sin pesos.")
    suma_n, suma_b = sum(e[1] or 0 for e in entradas), sum(e[2] or 0 for e in entradas)
    neto_t = _total_en_texto(r"(?i)(?:PESO\s+NETO|NET\s+WEIGHT|TOTAL\s+N\.?W\.?|^N\.?W\.?)", texto, dec)
    bruto_t = _total_en_texto(r"(?i)(?:PESO\s+BRUTO|GROSS\s+WEIGHT|TOTAL\s+G\.?W\.?|^G\.?W\.?)", texto, dec)
    for nombre, suma, tot in (("netos", suma_n, neto_t), ("brutos", suma_b, bruto_t)):
        if tot and abs(suma - tot) > 0.5:
            obs.append(f"Packing list: los {nombre} de los productos suman {suma:,.3f} y el total dice {tot:,.3f}.")
    bultos = _total_en_texto(r"(?i)(?:TOTAL\s+DE\s+BULTOS|TOTAL\s+PACKAGES|TOTAL\s+CAJAS|SAY\s+TOTAL)", texto, dec)
    if not bultos:
        m = re.search(r"(?i)\b(\d[\d.,]*)\s*(?:CARTONS|CTNS|PACKAGES|PKGS|BULTOS|CAJAS|BOXES)\b", texto)
        bultos = _valor(m.group(1), dec) if m else None
    m = re.search(r"(?i)^.*\bPALLETS?\b.*$", texto, re.M)
    if m and not TOTAL.search(m.group(0)):
        obs.append(f"Embalaje: «{m.group(0).strip()}». Revisar si los bultos del DUA son las cajas o los pallets.")
    return neto_t or round(suma_n, 3), bruto_t or round(suma_b, 3), bultos


def pesos_packing(renglones, lineas, obs):
    """Reparte el neto y el bruto del packing entre las líneas. Devuelve (neto_total, bruto_total, bultos)."""
    if not renglones or not lineas:
        return None, None, None
    por_item = pesos_por_item(renglones, lineas, obs)
    if por_item:
        return por_item
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
    dec = _decimal("\n".join(r.texto for r in renglones))
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
            filas.append([r, _valor(q.texto, dec) or 0, q.x0])
        if n and (len(nums) <= 5 or q):
            marcas.append((r.y, _valor(n.texto, dec) or 0, _valor(b.texto, dec) if b else None))
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
        importador = proveedor = ""
        try:
            from . import historial
            importador = historial.importador_en_texto(getattr(f, "texto", ""))
            proveedor = historial.proveedor_en_texto(getattr(f, "texto", ""))
        except Exception:
            pass
        for l in f.lineas:
            l.ncm, l.descripcion_es, l.motivo = sugerir(l.codigo, l.descripcion, l.ancho_mm,
                                                          getattr(l, "ncm_factura", ""), importador, proveedor)
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
    if re.search(r"(?i)\bPRO-?FORMA\b", getattr(f, "texto", "")):
        f.observaciones.append("Es una factura proforma: para el DUA hace falta la factura comercial definitiva.")
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
        for k in (0, 1):
            fila[d + k].number_format = "#,##0.00"
        for k in (4, 5):
            fila[d + k].number_format = "#,##0.00#"
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
    if ruta.suffix.lower() == ".ods":
        from .ods import guardar_ods
        guardar_ods(wb, ruta)
    else:
        wb.save(ruta)
    return ruta


FORMATOS = {"xlsx": [".xlsx"], "ods": [".ods"], "ambos": [".xlsx", ".ods"]}


def formato_preferido():
    """Formato elegido la última vez en la ventana: xlsx (Excel), ods (OpenOffice) o ambos."""
    import json
    try:
        f = json.loads(_ruta_preferencias().read_text(encoding="utf-8")).get("formato_planilla")
        return f if f in FORMATOS else "xlsx"
    except Exception:
        return "xlsx"


def guardar_formato(formato):
    import json
    try:
        r = _ruta_preferencias()
        datos = json.loads(r.read_text(encoding="utf-8")) if r.exists() else {}
        datos["formato_planilla"] = formato
        r.write_text(json.dumps(datos), encoding="utf-8")
    except Exception:
        pass


def _ruta_preferencias():
    import os
    d = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "elGonza"
    d.mkdir(parents=True, exist_ok=True)
    return d / "preferencias.json"


def planilla_de_carpeta(carpeta, salida=None, usar_ocr=True, avisar=print, formato=None):
    """Lee la carpeta y guarda la planilla (xlsx, ods o ambos). Devuelve (ruta, facturas) o (None, mensaje);
    con «ambos», ruta es la del .xlsx y el .ods queda al lado."""
    carpeta = Path(carpeta)
    try:
        from . import historial
        if historial.existe():
            historial.actualizar(avisar=avisar)
    except Exception:
        pass
    facturas = leer_carpeta(carpeta, usar_ocr=usar_ocr, avisar=avisar)
    if not facturas:
        return None, f"No encontré facturas con ítems en {carpeta}"
    base = Path(salida) if salida else carpeta / f"Planilla {carpeta.name}.xlsx"
    rutas = [generar_planilla(facturas, base.with_suffix(ext))
             for ext in FORMATOS.get(formato or formato_preferido(), [".xlsx"])]
    for r in rutas[1:]:
        avisar(f"  También en {r.name}")
    return rutas[0], facturas
