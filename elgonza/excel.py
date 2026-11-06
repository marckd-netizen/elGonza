"""Facturas y packing lists en Excel (.xlsx): se leen las celdas directo, sin OCR.

Cada hoja se clasifica (factura o packing list) por su título. La fila de encabezado se reconoce por los nombres de
columna (Description, Pcs/Qty, Price, Amount, HS/HSN, Net/Gross weight…). Un ítem puede ocupar varias filas (la
descripción partida arriba o abajo de la fila con los números): se agrupan por el número de ítem (Sno.) o, si no
hay, cada fila con números se lleva las filas de texto de arriba.
"""
import re
from dataclasses import dataclass, field

EXTENSIONES = (".xlsx", ".xlsm", ".xls")


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
    ("nro", r"^(s\.?\s*no\.?|sl\.?\s*no\.?|sr\.?\s*no\.?|no\.?|n[º°]|#|item|ítem|nro\.?|box\s*no\.?)$"),
    ("importe", r"amount|importe|total\s*(value|usd|us\$|price)|valor\s*total|^total$|^valor$|subtotal"),
    ("precio", r"price|per\s*(garment|pc|piece|unit|pair|set|doz)|unit\s*(value|cost)|precio|p\.?\s*unit|^fob|^rate$"),
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


def _roles(fila):
    roles = {}
    for j, v in enumerate(fila):
        r = _rol(v)
        if r == "tejido" and r in roles:
            r = "tejido2"           # tela y composición: las dos van a la descripción
        if r and r not in roles:
            roles[r] = j
    return roles


def _encabezado(filas):
    """(índice de la última fila del encabezado, {rol: columna}) o None. El encabezado puede ocupar dos filas
    («TOTAL» arriba y «PCS» abajo, «GROSS» y «WT(KG'S)»)."""
    mejor = None
    utiles_roles = {"cant", "total_cant", "precio", "importe", "descripcion", "neto_total", "bruto_total", "neto",
                    "bruto"}
    for i, fila in enumerate(filas[:60]):
        candidatos = [(i, _roles(fila))]
        if i + 1 < len(filas):
            sig = filas[i + 1]
            junto = [" ".join(_t(x) for x in (a, b) if x not in (None, ""))
                     if not any(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (a, b)) else None
                     for a, b in zip(list(fila) + [None] * (len(sig) - len(fila)),
                                     list(sig) + [None] * (len(fila) - len(sig)))]
            candidatos.append((i + 1, _roles(junto)))
        for fin, roles in candidatos:
            utiles = utiles_roles & set(roles)
            pesos_y_cant = bool({"neto", "bruto", "neto_total", "bruto_total"} & utiles) and \
                bool({"cant", "total_cant"} & utiles)
            if (len(utiles) >= 3 or pesos_y_cant) and (not mejor or len(roles) > len(mejor[1])):
                mejor = (fin, roles)
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


def _hojas_crudas(ruta):
    """[(nombre, filas)] de un .xlsx/.xlsm (openpyxl) o de un .xls viejo (xlrd)."""
    if str(ruta).lower().endswith(".xls"):
        import xlrd
        wb = xlrd.open_workbook(str(ruta))
        return [(s.name, [[None if v == "" else v for v in s.row_values(i)] for i in range(s.nrows)])
                for s in wb.sheets()]
    import openpyxl
    wb = openpyxl.load_workbook(ruta, data_only=True, read_only=True)
    try:
        return [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
    finally:
        wb.close()


def leer_hojas(ruta):
    """Hojas con facturas o packing lists de un .xlsx, .xlsm o .xls."""
    res = []
    for _, filas in _hojas_crudas(ruta):
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
        desde = _subencabezado(filas, i0, cols)
        _items(h, filas[desde:])
        res.append(h)
    return res


def _subencabezado(filas, i0, cols):
    """Encabezado de segundo nivel debajo del principal («STYLE NO. | STYLE NAME | COLOUR | ITEM DESCRIPTION»): el
    código va en su columna y la descripción junta las demás. Devuelve la fila donde empiezan los ítems."""
    for i in range(i0 + 1, min(i0 + 10, len(filas))):
        textos = {j: _t(v) for j, v in enumerate(filas[i]) if v not in (None, "")}
        if not textos or any(not isinstance(v, str) for v in filas[i] if v not in (None, "")):
            continue
        if any(re.fullmatch(r"\s*-?\d[\d.,]*\s*", t) for t in textos.values()):
            continue
        roles = {j: _rol(t) for j, t in textos.items()}
        cod = [j for j, t in textos.items() if roles[j] == "codigo" and re.search(r"(?i)\b(no\.?|code|c[oó]digo|ref|art)\b|#", t)]
        desc = [j for j in textos if j not in cod and j not in (cols.get("cant"), cols.get("total_cant"),
                                                                 cols.get("precio"), cols.get("importe"))]
        if cod and len(desc) >= 1 and len(textos) >= 3:
            cols["codigo"] = cod[0]
            cols["descripcion"] = desc[0]
            cols["desc_cols"] = desc
            return i + 1
    return i0 + 1


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

    def val(fila, rol, j=None):
        j = c.get(rol) if j is None else j
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
        if (es_total(fila) and any(_num(v) for v in fila if v not in (None, "") and not isinstance(v, str)
                                   or isinstance(v, str) and re.fullmatch(r"\s*\d[\d.,]*\s*", v))) or es_suma:
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
        if h.tipo == "packing" and cant_col is not None and "codigo" in c:
            # caja con varios productos (un renglón por producto y color) y el total de piezas solo en el primero:
            # la cantidad de cada renglón es la suma de sus talles, si los talles cierran con el total de la caja
            conocidas = {j for k, j in c.items() if k != "desc_cols" and isinstance(j, int)}

            def talles(f):
                return sum(_num(v) or 0 for j, v in enumerate(f) if c["codigo"] < j < cant_col and j not in conocidas
                           and v not in (None, "") and _num(v) is not None and not isinstance(v, str))
            con_cod = [f for f in b if _t(val(f, "codigo"))]
            tot = sum(_num(f[cant_col]) or 0 for f in b if cant_col < len(f))
            if len(con_cod) > len(filas_num) and all(talles(f) for f in con_cod) and \
                    abs(sum(talles(f) for f in con_cod) - tot) < 0.01:
                pesos = {rol: sum(_num(val(f, rol)) or 0 for f in b) for rol in ("neto_total", "bruto_total", "neto",
                                                                                   "bruto") if rol in c}
                nuevas = []
                for f in con_cod:
                    f = list(f) + [None] * max(0, cant_col + 1 - len(f))
                    q = talles(f)
                    f[cant_col] = q
                    for rol, w in pesos.items():
                        f[c[rol]] = round(w * q / tot, 3) if w else None
                    nuevas.append(f)
                filas_num = nuevas
        propios_cod = [_t(val(f, "codigo")) if h.tipo == "packing" and len(filas_num) > 1 else ""
                       for f in filas_num]
        if h.tipo == "packing" and len(filas_num) > 1:
            # una caja con varios productos y el peso escrito una sola vez: se reparte por cantidad
            for rol in ("neto_total", "bruto_total", "neto", "bruto"):
                vals = [_num(val(f, rol)) for f in filas_num]
                cants = [_num(f[cant_col]) if cant_col is not None and cant_col < len(f) else None for f in filas_num]
                if rol in c and any(vals) and not all(vals) and all(cants):
                    tot, sc = sum(v or 0 for v in vals), sum(cants)
                    for f, q in zip(filas_num, cants):
                        f[c[rol]] = round(tot * q / sc, 3)
        for f, cod in zip(filas_num, propios_cod):
            desc = " ".join(x for x in (_t(val(f, None, j)) for j in c["desc_cols"]) if x) if "desc_cols" in c \
                else junta("descripcion")
            it = {"nro": nro, "codigo": cod or junta("codigo"), "descripcion": desc,
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
