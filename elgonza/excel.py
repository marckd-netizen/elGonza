"""Facturas y packing lists en Excel (.xlsx): se leen las celdas directo, sin OCR.

Cada hoja se clasifica (factura o packing list) por su título. La fila de encabezado se reconoce por los nombres de
columna (Description, Pcs/Qty, Price, Amount, HS/HSN, Net/Gross weight…). Un ítem puede ocupar varias filas (la
descripción partida arriba o abajo de la fila con los números): se agrupan por el número de ítem (Sno.) o, si no
hay, cada fila con números se lleva las filas de texto de arriba.
"""
import re
from dataclasses import dataclass, field

EXTENSIONES = (".xlsx", ".xlsm")


def _t(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split())


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    from .numeros import num_libre
    s = _t(v).replace("$", "").replace("USD", "").strip()
    if re.fullmatch(r"-?\d+\.\d+", s):          # en una celda de texto «1725.500» es decimal, no miles
        return float(s)
    return num_libre(s) if s else None


# rol de cada columna según su encabezado (en minúsculas, sin saltos de línea)
ROLES = [
    ("nro", r"^(s\.?\s*no\.?|sl\.?\s*no\.?|sr\.?\s*no\.?|no\.?|n[º°]|#|item|ítem|nro\.?)$"),
    ("importe", r"amount|importe|total\s*(value|usd|us\$|price)|valor\s*total|^total$|^valor$|subtotal"),
    ("precio", r"price|per\s*(garment|pc|piece|unit|pair|set|doz)|unit\s*(value|cost)|precio|p\.?\s*unit|^fob"),
    ("total_cant", r"^total\s*(pcs|pieces|qty|quantity|units)$"),
    ("cant", r"^(pcs|qty|q'?ty|quantity|cantidad|cant\.?|qtde|units|pieces|pares|pairs)$|^(qty|quantity)\b"),
    ("hs", r"\b(hs|hsn|h\.s\.|ncm|hts|tariff|partida)\b"),
    ("descripcion", r"descrip|product|producto|goods|mercader|commodity"),
    ("codigo", r"article|art\.?\s*no|code|c[oó]digo|style|ref|sku|model|part\s*no|item\s*no"),
    ("tejido", r"fabric|composi|material|tejido"),
    ("neto_total", r"total\s*n(e)?t\.?\s*w|total\s*net|neto\s*total|total\s*nw"),
    ("bruto_total", r"total\s*gr(oss)?\.?\s*w|total\s*gross|bruto\s*total|total\s*gw"),
    ("neto", r"n(e)?t\.?\s*w(eigh)?t|net\s*weight|peso\s*neto|^n\.?\s*w\.?$"),
    ("bruto", r"gr(oss)?\.?\s*w(eigh)?t|gross\s*weight|peso\s*bruto|^g\.?\s*w\.?$"),
    ("bultos", r"no\.?\s*of\s*(box|ctn|carton|package)|^(ctns|cartons|boxes|bultos|cajas|packages)$"),
    ("ctn", r"ctn\s*no|carton\s*no|marks"),
]


def _rol(encabezado):
    h = _t(encabezado).lower()
    if not h:
        return None
    if re.search(r"per\s*(ctn|carton|box)", h):     # «Net Wt Per Ctn»: peso por caja, no el total de la fila
        return None
    for rol, patron in ROLES:
        if re.search(patron, h):
            return rol
    return None


@dataclass
class Hoja:
    tipo: str                 # factura | packing
    texto: str                # todo el texto de la hoja, para número, fecha, origen y HS
    columnas: dict            # rol -> índice
    items: list = field(default_factory=list)   # dicts con nro, codigo, descripcion, cant, precio, importe, hs, ...
    total_fila: list = None   # valores de la fila TOTAL


def _encabezado(filas):
    """(índice de la fila de encabezado, {rol: columna}) o None."""
    mejor = None
    for i, fila in enumerate(filas[:60]):
        roles = {}
        for j, v in enumerate(fila):
            r = _rol(v)
            if r == "tejido" and r in roles:
                r = "tejido2"           # tela y composición: las dos van a la descripción
            if r and r not in roles:
                roles[r] = j
        utiles = {"cant", "total_cant", "precio", "importe", "descripcion", "neto_total", "bruto_total", "neto",
                  "bruto"} & set(roles)
        if len(utiles) >= 3 and (not mejor or len(roles) > len(mejor[1])):
            mejor = (i, roles)
    return mejor


def _texto_encabezado(filas, hasta):
    """Texto de arriba de la tabla. A los rótulos sin valor («Invoice No. & Date») se les pega la celda de abajo."""
    lineas = []
    for i, fila in enumerate(filas[:hasta]):
        for j, v in enumerate(fila):
            t = str(v).strip() if v is not None else ""
            if not t:
                continue
            lineas.append(t)
            if re.search(r"(?i)invoice\s*(no|n[º°]|number|#)|factura\s*(n|nro)|date|fecha", t) and \
                    not re.search(r"\d", t):
                for k in range(i + 1, min(i + 4, hasta)):
                    w = filas[k][j] if j < len(filas[k]) else None
                    if w not in (None, ""):
                        rotulo = "INVOICE NO:" if re.search(r"(?i)invoice|factura", t) else "DATE:"
                        lineas.append(f"{rotulo} {str(w).strip()}")
                        if re.search(r"(?i)date|fecha", t) and rotulo != "DATE:":
                            m = re.search(r"(?i)(?:DT\.?|DATED?|FECHA)\s*:?\s*(\S+)", str(w))
                            if m:
                                lineas.append(f"DATE: {m.group(1)}")
                        break
    return "\n".join(lineas)


def leer_hojas(ruta):
    """Hojas con facturas o packing lists de un .xlsx."""
    import openpyxl
    wb = openpyxl.load_workbook(ruta, data_only=True, read_only=True)
    res = []
    for ws in wb.worksheets:
        filas = [list(r) for r in ws.iter_rows(values_only=True)]
        if not filas:
            continue
        todo = "\n".join(_t(v) for f in filas for v in f if v not in (None, ""))
        enc = _encabezado(filas)
        if not enc:
            continue
        i0, cols = enc
        arriba = " ".join(_t(v) for f in filas[:i0] for v in f if v not in (None, "")).upper()
        if "importe" not in cols and (re.search(r"PACKING\s*LIST|PACKAGE\s*LIST|LISTA DE EMPAQUE|ROMANEIO", arriba)
                                      or {"neto_total", "bruto_total", "neto", "bruto"} & set(cols)):
            tipo = "packing"
        elif "importe" in cols:
            tipo = "factura"
        else:
            continue
        filas = filas[:i0 + 1] + [x for f in filas[i0 + 1:] for x in _partir(f)]
        h = Hoja(tipo, _texto_encabezado(filas, i0) + "\n" + todo, cols)
        _items(h, filas[i0 + 1:])
        res.append(h)
    wb.close()
    return res


def _partir(fila):
    """Una fila con varias líneas por celda («1\\n2», «6960\\n24») se parte en varias filas, si las celdas con
    números tienen todas la misma cantidad de líneas. El texto que no se puede partir igual queda en todas."""
    partes = [str(v).split("\n") if isinstance(v, str) else [v] for v in fila]
    numericas = {len(p) for p, v in zip(partes, fila) if isinstance(v, str) and len(p) > 1
                 and all(re.fullmatch(r"\s*\d+(?:[.,]\d+)?\s*", x) for x in p)}
    if len(numericas) != 1:
        return [fila]
    k = numericas.pop()
    return [[(p[i] if len(p) == k else (v if i == 0 or not isinstance(v, (int, float)) else None))
             for p, v in zip(partes, fila)] for i in range(k)]


def _items(h, filas):
    c = h.columnas
    cant_col = c.get("total_cant", c.get("cant"))

    def val(fila, rol):
        j = c.get(rol)
        return fila[j] if j is not None and j < len(fila) else None

    def es_total(fila):
        return any(re.match(r"(?i)^\s*(total|sub\s*total|amount\s*charge|say\s)", _t(v)) for v in fila if v)

    # bloques: por número de ítem si la columna existe y se usa; si no, cada fila con números y el texto de arriba
    bloques, actual = [], []
    usa_nro = "nro" in c and any(_num(val(f, "nro")) for f in filas[:40])
    sumas = {}
    for fila in filas:
        # fila de totales sin la palabra TOTAL: solo números, y cada uno es la suma de su columna
        nums = {j: _num(v) for j, v in enumerate(fila) if v not in (None, "") and
                re.fullmatch(r"\s*-?\d[\d.,]*\s*", str(v)) and _num(v)}
        solo_numeros = all(v is None or j in nums for j, v in enumerate(fila))
        es_suma = solo_numeros and len(nums) >= 2 and all(abs(sumas.get(j, 0) - v) < 0.011 for j, v in nums.items())
        if es_total(fila) or es_suma:
            h.total_fila = fila
            break
        for j, v in nums.items():
            sumas[j] = sumas.get(j, 0) + v
        if not any(v not in (None, "") for v in fila):
            continue
        numeros = _num(val(fila, "importe")) if h.tipo == "factura" else \
            (_num(val(fila, "neto_total")) or _num(val(fila, "bruto_total")) or _num(fila[cant_col])
             if cant_col is not None and cant_col < len(fila) else None)
        if usa_nro:
            if _num(val(fila, "nro")) and actual:
                bloques.append(actual)
                actual = []
            actual.append(fila)
        else:
            actual.append(fila)
            if numeros:
                bloques.append(actual)
                actual = []
    if actual:
        if usa_nro or not bloques:
            bloques.append(actual)
        else:
            bloques[-1].extend(actual)   # texto que sigue a la última fila con números
    for b in bloques:
        nro = next((_t(val(f, "nro")) for f in b if _num(val(f, "nro"))), "")

        def junta(rol):
            partes = []
            for f in b:
                t = _t(val(f, rol))
                if t and (not partes or partes[-1] != t):
                    partes.append(t)
            return " ".join(partes)
        filas_num = [f for f in b if (_num(val(f, "importe")) if h.tipo == "factura"
                                     else (_num(val(f, "neto_total")) or _num(val(f, "bruto_total"))
                                           or (_num(f[cant_col]) if cant_col is not None and cant_col < len(f)
                                               else None)))]
        for f in filas_num:
            it = {"nro": nro, "codigo": junta("codigo"), "descripcion": junta("descripcion"),
                  "tejido": " ".join(x for x in (junta("tejido"), junta("tejido2")) if x), "hs": re.sub(r"\D", "", next((_t(val(x, "hs")) for x in b
                                                                          if _t(val(x, "hs"))), "")),
                  "cant": _num(f[cant_col]) if cant_col is not None and cant_col < len(f) else None,
                  "precio": _num(val(f, "precio")), "importe": _num(val(f, "importe")),
                  "neto": _num(val(f, "neto_total")), "bruto": _num(val(f, "bruto_total")),
                  "bultos": _num(val(f, "bultos"))}
            if it["neto"] is None and "neto_total" not in c:
                it["neto"] = _num(val(f, "neto"))
            if it["bruto"] is None and "bruto_total" not in c:
                it["bruto"] = _num(val(f, "bruto"))
            h.items.append(it)


def total_de(h, rol):
    j = h.columnas.get(rol)
    if h.total_fila is None or j is None or j >= len(h.total_fila):
        return None
    return _num(h.total_fila[j])
