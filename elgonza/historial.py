"""Base de antecedentes de NCM armada con los despachos ya hechos.

Dos fuentes, guardadas en %LOCALAPPDATA%\\elGonza\\antecedentes.sqlite:

1. Los borradores de Mega6 (Permiso6\\respaldo\\Borrador*.pdf): importador, proveedor, descripción y NCM de cada
   ítem. Se leen una vez; después solo los nuevos o cambiados.
2. Lo aprendido al revisar un despacho: cada ítem del borrador dice qué líneas de qué factura agrupa
   («It:1 al 3/Fac. 976»), así que el código de producto de esas líneas queda unido al NCM declarado.

Para sugerir: primero el mismo código de producto (del mismo proveedor si se puede); si no, la descripción más
parecida (traducida del inglés con un glosario), con más peso para el mismo importador y el mismo proveedor.
"""
import os
import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path

VERSION = 1


def ruta_base():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), ".cache")
    d = Path(base) / "elGonza"
    d.mkdir(parents=True, exist_ok=True)
    return d / "antecedentes.sqlite"


def conectar(ruta=None):
    con = sqlite3.connect(str(ruta or ruta_base()))
    con.executescript("""
        CREATE TABLE IF NOT EXISTS archivos (nombre TEXT PRIMARY KEY, firma TEXT);
        CREATE TABLE IF NOT EXISTS items (archivo TEXT, carpeta TEXT, fecha TEXT, rut TEXT, importador TEXT,
                                          proveedor TEXT, ncm TEXT, descripcion TEXT);
        CREATE INDEX IF NOT EXISTS items_archivo ON items(archivo);
        CREATE TABLE IF NOT EXISTS codigos (codigo TEXT, proveedor TEXT, importador TEXT, ncm TEXT,
                                            descripcion TEXT, carpeta TEXT, PRIMARY KEY (codigo, proveedor, carpeta));
    """)
    return con


def existe(ruta=None):
    r = Path(ruta or ruta_base())
    if not r.exists():
        return False
    with conectar(r) as con:
        return con.execute("SELECT COUNT(*) FROM items").fetchone()[0] > 0


# --- armar la base con los borradores ---------------------------------------
def actualizar(raiz=None, avisar=None, ruta=None, limite=None):
    """Lee los borradores nuevos o cambiados de Permiso6\\respaldo. Devuelve cuántos leyó."""
    from .borrador import extraer_texto_pdf, parsear
    from .mega6 import raiz_mega6
    raiz = Path(raiz) if raiz else raiz_mega6()
    carpeta = raiz / "respaldo" if raiz else None
    if not carpeta or not carpeta.is_dir():
        return 0
    with os.scandir(carpeta) as it:
        archivos = [(e.name, f"{e.stat().st_size}-{int(e.stat().st_mtime)}", e.path) for e in it
                    if e.name.lower().startswith("borrador") and e.name.lower().endswith((".pdf", ".txt"))]
    con = conectar(ruta)
    vistos = dict(con.execute("SELECT nombre, firma FROM archivos"))
    nuevos = [a for a in archivos if vistos.get(a[0]) != a[1]]
    if limite:
        nuevos = nuevos[:limite]
    if nuevos and avisar:
        avisar(f"Base de antecedentes: leyendo {len(nuevos)} borradores de Mega6"
               + (" (la primera vez tarda unos minutos)…" if not vistos else "…"))
    for n, (nombre, firma, ruta_a) in enumerate(nuevos, 1):
        try:
            texto = Path(ruta_a).read_text(encoding="utf-8", errors="replace") if nombre.lower().endswith(".txt") \
                else extraer_texto_pdf(ruta_a)
            b = parsear(texto)
        except Exception:
            b = None
        con.execute("DELETE FROM items WHERE archivo = ?", (nombre,))
        if b:
            con.executemany("INSERT INTO items VALUES (?,?,?,?,?,?,?,?)",
                            [(nombre, b.carpeta, b.fecha, b.importador_rut, b.importador, b.proveedor, it.ncm,
                              it.descripcion.rstrip(".- ")) for it in b.items if it.ncm and it.descripcion])
        con.execute("INSERT OR REPLACE INTO archivos VALUES (?, ?)", (nombre, firma))
        if n % 200 == 0:
            con.commit()
            if avisar:
                avisar(f"  {n} de {len(nuevos)}…")
    con.commit()
    con.close()
    _cache.clear()
    return len(nuevos)


# --- aprender en la revisión -------------------------------------------------
def aprender(borrador, docs, ruta=None):
    """Une los códigos de producto de las facturas con el NCM de cada ítem del borrador. Devuelve cuántos guardó."""
    from .borrador import referencias_por_factura
    from .planilla import _norm, leer_items, paginas_de
    por_factura = []   # (documento, líneas)
    for d in docs:
        if d.tipo != "factura" or not Path(d.ruta).is_file() or Path(d.ruta).suffix.lower() != ".pdf":
            continue
        try:
            renglones = [r for p in paginas_de(d.ruta, usar_ocr=False) for r in p]
            lineas = leer_items(renglones)
        except Exception:
            continue
        if lineas:
            por_factura.append((d, lineas))
    if not por_factura:
        return 0
    filas = []
    for it in borrador.items:
        for fac, nros in referencias_por_factura(it).items():
            lineas = next((ls for d, ls in por_factura if _norm(fac) and _norm(fac) in _norm(d.texto or "")), None)
            if not lineas:
                continue
            numeradas = {int(l.nro): l for l in lineas if str(l.nro).isdigit()}
            if len(numeradas) != len(lineas):
                numeradas = dict(enumerate(lineas, 1))
            for n in nros:
                l = numeradas.get(n)
                if l and l.codigo:
                    filas.append((_norm(l.codigo), _clave_nombre(borrador.proveedor), borrador.importador,
                                  it.ncm, it.descripcion.rstrip(".- "), borrador.carpeta))
    if filas:
        with conectar(ruta) as con:
            con.executemany("INSERT OR REPLACE INTO codigos VALUES (?,?,?,?,?,?)", filas)
        _cache.clear()
    return len(filas)


# --- sugerir ------------------------------------------------------------------
GLOSARIO = {
    "VALVE": "VALVULA", "VALVES": "VALVULA", "BRASS": "LATON", "BRONZE": "BRONCE", "STEEL": "ACERO",
    "STAINLESS": "INOXIDABLE", "IRON": "HIERRO", "CAST": "FUNDIDO", "COPPER": "COBRE", "ALUMINIUM": "ALUMINIO",
    "ALUMINUM": "ALUMINIO", "PLASTIC": "PLASTICO", "RUBBER": "CAUCHO", "WOOD": "MADERA", "WOODEN": "MADERA",
    "GLASS": "VIDRIO", "PAPER": "PAPEL", "FILM": "PELICULA", "ADHESIVE": "ADHESIVO", "SELF": "AUTO",
    "CHECK": "RETENCION", "BALL": "ESFERICA", "SAFETY": "SEGURIDAD", "FOOT": "PIE", "GATE": "ESCLUSA",
    "TAP": "CANILLA", "TAPS": "CANILLA", "FAUCET": "GRIFO", "BIBCOCK": "CANILLA", "BIBCOCKS": "CANILLA",
    "PIPE": "TUBO", "PIPES": "TUBO", "TUBE": "TUBO", "HOSE": "MANGUERA", "FITTING": "ACCESORIO",
    "FITTINGS": "ACCESORIO", "ELBOW": "CODO", "TEE": "TEE", "UNION": "UNION", "NIPPLE": "NIPLE", "PUMP": "BOMBA",
    "PUMPS": "BOMBA", "MOTOR": "MOTOR", "ENGINE": "MOTOR", "CABLE": "CABLE", "WIRE": "ALAMBRE", "LAMP": "LAMPARA",
    "LIGHT": "LUZ", "BULB": "LAMPARA", "SWITCH": "INTERRUPTOR", "PLUG": "ENCHUFE", "SOCKET": "TOMACORRIENTE",
    "BATTERY": "BATERIA", "CHARGER": "CARGADOR", "SCREW": "TORNILLO", "SCREWS": "TORNILLO", "BOLT": "PERNO",
    "NUT": "TUERCA", "WASHER": "ARANDELA", "NAIL": "CLAVO", "TOOL": "HERRAMIENTA", "TOOLS": "HERRAMIENTA",
    "KNIFE": "CUCHILLO", "SCISSORS": "TIJERA", "HAMMER": "MARTILLO", "DRILL": "TALADRO", "SAW": "SIERRA",
    "TABLE": "MESA", "CHAIR": "SILLA", "CHAIRS": "SILLA", "SOFA": "SOFA", "BED": "CAMA", "WARDROBE": "ROPERO",
    "SHELF": "ESTANTE", "CABINET": "MUEBLE", "FURNITURE": "MUEBLE", "MATTRESS": "COLCHON", "PILLOW": "ALMOHADA",
    "TOWEL": "TOALLA", "SHEET": "SABANA", "BLANKET": "MANTA", "CURTAIN": "CORTINA", "CARPET": "ALFOMBRA",
    "SHIRT": "CAMISA", "SHIRTS": "CAMISA", "TROUSERS": "PANTALON", "PANTS": "PANTALON", "DRESS": "VESTIDO",
    "JACKET": "CAMPERA", "SHOES": "CALZADO", "SHOE": "CALZADO", "BOOTS": "BOTA", "SOCKS": "MEDIA", "BAG": "BOLSO",
    "BAGS": "BOLSO", "COTTON": "ALGODON", "POLYESTER": "POLIESTER", "KNITTED": "PUNTO", "WOVEN": "TEJIDO",
    "FABRIC": "TEJIDO", "YARN": "HILADO", "LEATHER": "CUERO", "TOY": "JUGUETE", "TOYS": "JUGUETE", "DOLL": "MUNECA",
    "GAME": "JUEGO", "BOOK": "LIBRO", "BOOKS": "LIBRO", "PRINTED": "IMPRESO", "LABEL": "ETIQUETA",
    "LABELS": "ETIQUETA", "BOX": "CAJA", "BOXES": "CAJA", "BOTTLE": "BOTELLA", "BOTTLES": "BOTELLA", "CAP": "TAPA",
    "CAPS": "TAPA", "LID": "TAPA", "CONTAINER": "ENVASE", "PACKAGING": "ENVASE", "COFFEE": "CAFE", "TEA": "TE",
    "SUGAR": "AZUCAR", "OIL": "ACEITE", "WINE": "VINO", "BEER": "CERVEZA", "JUICE": "JUGO", "MILK": "LECHE",
    "CHEESE": "QUESO", "MEAT": "CARNE", "BEEF": "BOVINA", "PORK": "PORCINA", "CHICKEN": "POLLO", "FISH": "PESCADO",
    "FROZEN": "CONGELADO", "FRESH": "FRESCO", "DRIED": "SECO", "RICE": "ARROZ", "FLOUR": "HARINA", "SALT": "SAL",
    "SAUCE": "SALSA", "COOKIES": "GALLETITA", "CHOCOLATE": "CHOCOLATE", "CANDY": "CARAMELO", "SOAP": "JABON",
    "SHAMPOO": "CHAMPU", "CREAM": "CREMA", "PERFUME": "PERFUME", "MEDICINE": "MEDICAMENTO", "SYRINGE": "JERINGA",
    "GLOVES": "GUANTE", "MASK": "MASCARA", "PHONE": "TELEFONO", "MOBILE": "CELULAR", "CELLULAR": "CELULAR",
    "COMPUTER": "COMPUTADORA", "LAPTOP": "COMPUTADORA", "MONITOR": "MONITOR", "PRINTER": "IMPRESORA",
    "KEYBOARD": "TECLADO", "MOUSE": "MOUSE", "CAMERA": "CAMARA", "SPEAKER": "PARLANTE", "HEADPHONES": "AURICULAR",
    "TELEVISION": "TELEVISOR", "TV": "TELEVISOR", "REFRIGERATOR": "REFRIGERADOR", "FRIDGE": "REFRIGERADOR",
    "FREEZER": "CONGELADOR", "WASHING": "LAVAR", "MACHINE": "MAQUINA", "MACHINES": "MAQUINA", "FAN": "VENTILADOR",
    "HEATER": "CALEFACTOR", "AIR": "AIRE", "CONDITIONER": "ACONDICIONADO", "OVEN": "HORNO", "STOVE": "COCINA",
    "KITCHEN": "COCINA", "BATHROOM": "BANO", "SINK": "PILETA", "TOILET": "INODORO", "SHOWER": "DUCHA",
    "MIRROR": "ESPEJO", "DOOR": "PUERTA", "WINDOW": "VENTANA", "LOCK": "CERRADURA", "KEY": "LLAVE", "HANDLE": "MANIJA",
    "SPARE": "REPUESTO", "PARTS": "PARTE", "PART": "PARTE", "ACCESSORIES": "ACCESORIO", "FILTER": "FILTRO",
    "TIRE": "NEUMATICO", "TIRES": "NEUMATICO", "TYRE": "NEUMATICO", "WHEEL": "RUEDA", "CAR": "AUTOMOVIL",
    "VEHICLE": "VEHICULO", "BICYCLE": "BICICLETA", "PAINT": "PINTURA", "INK": "TINTA", "GLUE": "COLA",
    "RESIN": "RESINA", "POLYETHYLENE": "POLIETILENO", "POLYPROPYLENE": "POLIPROPILENO", "PVC": "PVC",
    "FERTILIZER": "FERTILIZANTE", "SEEDS": "SEMILLA", "PLANT": "PLANTA", "CHEMICAL": "QUIMICO", "ACID": "ACIDO",
    "WHITE": "BLANCO", "BLACK": "NEGRO", "MATTE": "MATE", "GLOSS": "BRILLANTE", "TRANSPARENT": "TRANSPARENTE",
}
VACIAS = set("""DE DEL LA LAS LOS EL EN CON PARA POR SIN UNA UNO UN Y O A AL SU SUS LAS DEMAS LOS DEMAS QUE COMO
TIPO TIPOS MARCA MODELO CODIGO REF THE WITH AND FOR OF IN TO ON BY FROM NEW PCS SET SETS UNIT UNITS SIZE COLOR
MM CM INCH TOTAL ITEM""".split())


def _sin_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn")


def palabras(texto):
    """Raíces (5 letras) de las palabras significativas, traducidas al español."""
    t = _sin_tildes(texto).upper()
    res = set()
    for w in re.findall(r"[A-Z]{3,}", t):
        w = GLOSARIO.get(w, w)
        if w in VACIAS:
            continue
        for x in w.split():
            res.add(x[:5])
    return res


def _clave_nombre(nombre):
    """Primeras dos palabras significativas del nombre de una empresa, para comparar proveedores."""
    ws = [w for w in re.findall(r"[A-Z0-9]{3,}", _sin_tildes(nombre).upper())
          if w not in {"CO", "LTD", "LTDA", "SA", "S.A", "INC", "LLC", "GMBH", "SRL", "CORP", "COMPANY", "LIMITED",
                       "INDUSTRIAL", "TRADING", "IMPORT", "EXPORT", "INTERNATIONAL"}]
    return " ".join(ws[:2])


_cache = {}


def _datos(ruta=None):
    r = str(ruta or ruta_base())
    if r in _cache:
        return _cache[r]
    if not Path(r).exists():
        _cache[r] = None
        return None
    with conectar(r) as con:
        items = con.execute("SELECT carpeta, importador, proveedor, ncm, descripcion FROM items").fetchall()
        codigos = con.execute("SELECT codigo, proveedor, importador, ncm, descripcion, carpeta FROM codigos").fetchall()
    # se agrupan descripciones iguales: (importador, proveedor, ncm, descripcion) -> carpetas
    grupos = defaultdict(list)
    for carpeta, imp, prov, ncm, desc in items:
        grupos[(imp, _clave_nombre(prov), ncm, desc)].append(carpeta)
    filas = [(k, palabras(k[3]), v) for k, v in grupos.items()]
    indice = defaultdict(list)
    for i, (_, ws, _) in enumerate(filas):
        for w in ws:
            indice[w].append(i)
    _cache[r] = (filas, indice, len(filas) or 1, codigos)
    return _cache[r]


def importador_en_texto(texto, ruta=None):
    """Importador de la base cuyo nombre aparece en el texto de la factura ("TO: EFUSION S.A.")."""
    d = _datos(ruta)
    if not d:
        return ""
    t = " " + re.sub(r"[^A-Z0-9]+", " ", _sin_tildes(texto).upper()) + " "
    mejor = ""
    for imp in {k[0] for k, _, _ in d[0]}:
        clave = _clave_nombre(imp)
        if clave and f" {clave} " in t and len(clave) > len(mejor):
            mejor = imp
    return mejor


def proveedor_en_texto(texto, ruta=None):
    """Proveedor de la base cuyo nombre aparece en el texto de la factura."""
    d = _datos(ruta)
    if not d:
        return ""
    t = " " + re.sub(r"[^A-Z0-9]+", " ", _sin_tildes(texto).upper()) + " "
    claves = {k[1] for k, _, _ in d[0]} | {c[1] for c in d[3]}
    return max((c for c in claves if c and f" {c} " in t), key=len, default="")


def buscar(codigo, descripcion, importador="", proveedor="", ruta=None):
    """(ncm, descripción del despacho, motivo, puntaje) del mejor antecedente, o None."""
    import math
    from .planilla import _norm
    d = _datos(ruta)
    if not d:
        return None
    filas, indice, total, codigos = d
    prov = _clave_nombre(proveedor)
    c = _norm(codigo)
    if c:
        mismos = [x for x in codigos if x[0] == c]
        if prov:
            mismos = [x for x in mismos if x[1] == prov] or mismos
        if mismos:
            votos = defaultdict(list)
            for x in mismos:
                votos[x[3]].append(x[5])
            ncm, carpetas = max(votos.items(), key=lambda kv: len(kv[1]))
            otros = f" (también se declaró en {', '.join(k for k in votos if k != ncm)})" if len(votos) > 1 else ""
            desc_es = next(x[4] for x in mismos if x[3] == ncm)
            return ncm, desc_es, (f"Antecedente: el código {codigo} se declaró en {ncm} en "
                          f"{len(carpetas)} despacho{'s' if len(carpetas) > 1 else ''} "
                          f"(carpeta {', '.join(sorted(set(carpetas))[-3:])}){otros}."), 1.0
    ws = palabras(descripcion)
    if not ws:
        return None
    idf = {w: math.log(1 + total / (1 + len(indice.get(w, ())))) for w in ws}
    peso_total = sum(idf.values()) or 1
    por_ncm = defaultdict(lambda: [0.0, None, 0])
    candidatas = {i for w in ws for i in indice.get(w, ())}
    for i in candidatas:
        (imp, p, ncm, desc), dws, carpetas = filas[i]
        comunes = ws & dws
        if not comunes:
            continue
        s = sum(idf[w] for w in comunes) / peso_total
        s *= min(1.0, 0.5 + len(comunes) / (len(dws) or 1))   # castiga descripciones largas que coinciden poco
        if importador and imp == importador:
            s *= 1.4
        if prov and p == prov:
            s *= 1.3
        acc = por_ncm[ncm]
        if s > acc[0]:
            acc[0], acc[1] = s, (desc, imp, carpetas)
        acc[2] += len(carpetas)
    if not por_ncm:
        return None
    ncm, (s, (desc, imp, carpetas), veces) = max(por_ncm.items(), key=lambda kv: (round(kv[1][0], 3), kv[1][2]))
    otros = sorted(((v[0], k) for k, v in por_ncm.items() if k != ncm and v[0] >= s * 0.8), reverse=True)[:2]
    motivo = (f"Antecedente de Mega6: «{desc}» de {imp} (carpeta {carpetas[-1]}) se declaró en {ncm}"
              f"{'; ' + str(veces) + ' ítems con ese NCM' if veces > 1 else ''}.")
    if otros:
        motivo += " Otros parecidos: " + ", ".join(k for _, k in otros) + "."
    return ncm, desc, motivo, min(s, 1.0)
