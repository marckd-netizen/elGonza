"""Lectura del borrador de DUA (PDF con texto) a una estructura de datos."""
import re
from dataclasses import dataclass, field

from .numeros import num

NUM = r"-?[\d.]+,\d+"
NCM_ITEM = r"(\d+)\s+(\d{4}\.\d{2}(?:\.\d{2}){0,2})\s+(.*)$"


@dataclass
class Factura:
    tipo: str            # FACN = comercial, FACC = flete / otros
    numero: str
    fecha: str
    origen: str = ""
    valor: float = 0.0
    moneda: str = ""
    gastos: float = 0.0
    flete: float = 0.0
    flete_moneda: str = ""
    seguro: float = 0.0
    seguro_moneda: str = ""
    cond_vta: str = ""
    lugar: str = ""
    cant_items: int = 0


@dataclass
class Item:
    nro: int
    ncm: str
    tipo_bulto: str = ""
    bultos: float = 0.0
    neto: float = 0.0
    bruto: float = 0.0
    facturas: list = field(default_factory=list)
    doc_carga: str = ""
    subregimen: str = ""
    subregimen_texto: str = ""
    origen: str = ""
    procedencia: str = ""
    adquisicion: str = ""
    unidad_fisica: str = ""
    cant_fisica: float = 0.0
    unidad_comercial: str = ""
    cant_comercial: float = 0.0
    cif: float = 0.0
    gastos: float = 0.0
    valor_dna: float = 0.0
    valor_dna_aj: float = 0.0
    metodo: str = ""
    forma_pago: str = ""
    descripcion: str = ""
    refs: str = ""          # texto "It:1 al 3/Fac. 976"
    estado: str = ""
    tasas: dict = field(default_factory=dict)
    certificados: list = field(default_factory=list)


@dataclass
class Borrador:
    carpeta: str = ""
    fecha: str = ""
    importador_rut: str = ""
    importador: str = ""
    aduana: str = ""
    regimen: str = ""
    proveedor: str = ""
    tipo_import: str = ""
    cant_fac: int = 0
    tot_fac: float = 0.0
    moneda: str = ""
    moneda_nombre: str = ""
    arbitraje: float = 1.0
    tc: float = 0.0
    nat_transac: str = ""
    cond_vta: str = ""
    medio_transporte: str = ""
    pais_transporte: str = ""
    transportista: str = ""
    aduana_ingreso: str = ""
    total_bultos: float = 0.0
    peso_bruto: float = 0.0
    peso_neto: float = 0.0
    total_unidades: float = 0.0
    cif: float = 0.0
    gastos: float = 0.0
    vna: float = 0.0
    vna_pesos: float = 0.0
    contenedores: list = field(default_factory=list)   # [(numero, precinto, medida)]
    cant_contenedores: int = 0
    autoliquidacion: dict = field(default_factory=dict)
    liquidacion_dna: dict = field(default_factory=dict)
    observaciones: str = ""
    certificados: list = field(default_factory=list)   # [(codigo, numero)]
    facturas: list = field(default_factory=list)
    items: list = field(default_factory=list)

    @property
    def facturas_comerciales(self):
        return [f for f in self.facturas if f.tipo == "FACN"]

    @property
    def facturas_flete(self):
        return [f for f in self.facturas if f.tipo == "FACC"]


def extraer_texto_pdf(ruta):
    """Texto del borrador respetando el orden de las columnas."""
    import pdfplumber
    with pdfplumber.open(ruta) as pdf:
        return "\n".join((p.extract_text(layout=True) or "") for p in pdf.pages)


def _lineas(texto):
    out = []
    for linea in texto.splitlines():
        l = re.sub(r"\s+", " ", linea).strip()
        if not l:
            continue
        if "Borrador D.U.A." in l or l.startswith("- Ref.Cli") or set(l) <= set("-+| "):
            continue
        if l.startswith("|Item") or l.startswith("| Factura") or l.startswith("| Nro.Doc"):
            continue
        out.append(l)
    return out


def _solo_nombre(s):
    """Separa la razón social de la dirección que viene pegada en la misma línea."""
    m = re.match(r"(.*?\b(?:S\.A\.|S\.R\.L\.|SRL|LTDA\.?|S\.A\.S\.|SAS|SOCIEDAD ANONIMA))(?=\s|$)", s)
    if m:
        return m.group(1).strip()
    m = re.match(r"(.*?)\s+(?:AV\.|AVDA|AVENIDA|RUTA|CALLE|BV\.|BLVR|CAMINO)\b", s, re.I)
    return m.group(1).strip() if m else s


def _busca(patron, texto, grupo=1, conv=None, defecto=None):
    m = re.search(patron, texto)
    if not m:
        return defecto
    v = m.group(grupo).strip()
    return conv(v) if conv else v


def _bloque_tributos(lineas, inicio, fin_marcas):
    """Lee pares 'Nombre :$ 1.234,00' desde la línea inicio hasta 'Total a Pagar'."""
    res = {}
    for l in lineas[inicio:]:
        for nombre, valor in re.findall(r"([A-Za-zÁÉÍÓÚáéíóú.()/\d ]+?)\s*:\$\s*(" + NUM + ")", l):
            nombre = nombre.replace("AUTOLIQUIDACION:", "").replace("LIQUIDACION DNA:", "").strip()
            res[nombre] = num(valor)
        if any(m in l for m in fin_marcas):
            break
    return res


def parsear(texto):
    b = Borrador()
    lineas = _lineas(texto)
    todo = "\n".join(lineas)

    b.carpeta = _busca(r"Carpeta\s*:\s*(\d+)", texto, defecto="")
    b.fecha = _busca(r"Fecha:\s*(\d{1,2}/\d{2}/\d{4})", todo, defecto="")
    m = re.search(r"Importador\.:\s*\d+\s*R\.U\.T\.\s*(\d+)\s+(.+?)(?:\s{2,}|\s+Aduana:|$)", todo, re.M)
    if m:
        b.importador_rut = m.group(1)
        b.importador = _solo_nombre(m.group(2).strip())
    b.aduana = _busca(r"Aduana:\s*(\d+\s+[A-Z ]+)", todo, defecto="")
    b.regimen = _busca(r"Régimen:\s*(\d+)", todo, defecto="")
    b.proveedor = _busca(r"Proveedor\s*:\s*\d+\s+\w+\s+(.+?)\s+Fecha:", todo, defecto="")
    b.tipo_import = _busca(r"Tipo Import:\s*(\w)", todo, defecto="")
    b.cant_fac = _busca(r"Cant\.Fac:\s*(\d+)", todo, conv=int, defecto=0)
    b.tot_fac = _busca(r"Tot\.Fac:\s*(" + NUM + ")", todo, conv=num, defecto=0.0)
    m = re.search(r"Moneda:\s*(\d{3})\s+(.+?)\s+Arb:", todo)
    if m:
        b.moneda, b.moneda_nombre = m.group(1), m.group(2)
    b.arbitraje = _busca(r"Arb:\s*(" + NUM + ")", todo, conv=num, defecto=1.0)
    b.tc = _busca(r"T/C:\s*(" + NUM + ")", todo, conv=num, defecto=0.0)
    b.nat_transac = _busca(r"Nat\.Transac:\s*(\d+)", todo, defecto="")
    b.cond_vta = _busca(r"Cond\.Vta:\s*([A-Z]{3})", todo, defecto="")
    b.medio_transporte = _busca(r"Med\.Transp:\s*(\d+\s+[A-Z ]+?)\s+País", todo, defecto="")
    b.pais_transporte = _busca(r"Med\.Transp:.*País\s*:\s*(\d+\s+[A-Z ]+)", todo, defecto="")
    b.transportista = _busca(r"Nombre Transp:\s*(.+?)\s+Depósito", todo, defecto="")
    b.aduana_ingreso = _busca(r"Adu\.Ingreso:\s*(\d+\s+[A-Z ]+?)\s+Lugar", todo, defecto="")
    b.total_bultos = _busca(r"Total Bultos:\s*(" + NUM + ")", todo, conv=num, defecto=0.0)
    b.peso_bruto = _busca(r"Total Peso Bruto:\s*(" + NUM + ")", todo, conv=num, defecto=0.0)
    b.peso_neto = _busca(r"Total Peso Neto:\s*(" + NUM + ")", todo, conv=num, defecto=0.0)
    b.total_unidades = _busca(r"Total de Unidades:\s*(" + NUM + ")", todo, conv=num, defecto=0.0)
    b.cif = _busca(r"Totales: CIF U\$S:\s*(" + NUM + ")", todo, conv=num, defecto=0.0)
    b.gastos = _busca(r"Gastos/Aj U\$S:\s*(" + NUM + ")", todo, conv=num, defecto=0.0)
    b.vna = _busca(r"VNA U\$S:\s*(" + NUM + ")", todo, conv=num, defecto=0.0)
    b.vna_pesos = _busca(r"Total VNA \$:\s*(" + NUM + ")", todo, conv=num, defecto=0.0)
    for m in re.finditer(r"Contenedor:\s*(\S+)\s+Precinto:\s*(\S*)\s+Medida:\s*(\d+)", todo):
        b.contenedores.append((m.group(1), m.group(2), m.group(3)))
    b.cant_contenedores = _busca(r"Cantidad de Contenedores:\s*(\d+)", todo, conv=int, defecto=0)
    b.observaciones = _busca(r"Observaciones\s*:\s*(.+)", todo, defecto="")

    # Bloques de tributos
    for i, l in enumerate(lineas):
        if l.startswith("AUTOLIQUIDACION:") and not b.autoliquidacion:
            b.autoliquidacion = _bloque_tributos(lineas, i, ["Total a Pagar"])
        elif l.startswith("LIQUIDACION DNA:") and not b.liquidacion_dna:
            b.liquidacion_dna = _bloque_tributos(lineas, i, ["Total a Pagar"])

    # Facturas del cabezal: línea FACN/FACC + línea siguiente con condición, lugar y cantidad de ítems
    for i, l in enumerate(lineas):
        m = re.match(r"(FAC[NC])\s+(\S+)\s+(\d{1,2}/\d{2}/\d{4})\s+(\d{3}/\d{0,3})\s*(.*)$", l)
        if not m:
            continue
        resto = m.group(5)
        f = Factura(tipo=m.group(1), numero=m.group(2), fecha=m.group(3), origen=m.group(4))
        importes = re.findall(r"(" + NUM + r")\s+(\d{3})", resto)
        if importes:
            f.valor, f.moneda = num(importes[0][0]), importes[0][1]
        if len(importes) >= 4:
            f.gastos = num(importes[1][0])
            f.flete, f.flete_moneda = num(importes[2][0]), importes[2][1]
            f.seguro, f.seguro_moneda = num(importes[3][0]), importes[3][1]
        elif not importes:
            v = re.search(NUM, resto)
            f.valor = num(v.group(0)) if v else 0.0
        sig = lineas[i + 1] if i + 1 < len(lineas) else ""
        m2 = re.match(r"(?:\d{6,}\s+)?([A-Z]{3})\s+(?:[A-Z]{3}\s+)?(.+?)(?:\s+(\d+))?$", sig)
        if m2:
            f.cond_vta = m2.group(1)
            f.lugar = m2.group(2).strip()
            f.cant_items = int(m2.group(3)) if m2.group(3) else 0
        b.facturas.append(f)

    # Certificados de cabezal (antes del primer ítem)
    idx_items = [i for i, l in enumerate(lineas) if re.match(NCM_ITEM, l)]
    primero = idx_items[0] if idx_items else len(lineas)
    for l in lineas[:primero]:
        m = re.match(r"Cert:\s*(\w+).*?Nro:\s*(\S+)", l)
        if m:
            b.certificados.append((m.group(1), m.group(2)))

    # Ítems
    for k, ini in enumerate(idx_items):
        fin = idx_items[k + 1] if k + 1 < len(idx_items) else len(lineas)
        b.items.append(_parsear_item(lineas[ini:fin]))
    return b


def _parsear_item(ls):
    l0 = ls[0]
    m = re.match(NCM_ITEM, l0)
    it = Item(nro=int(m.group(1)), ncm=m.group(2))
    nums = re.findall(NUM, m.group(3))
    if len(nums) >= 3:
        it.bultos, it.neto, it.bruto = (num(x) for x in nums[-3:])
    tb = re.search(r"\b([A-Z]{2,3})\s+[A-Z/ ]+?\s+" + NUM, m.group(3))
    it.tipo_bulto = tb.group(1) if tb else ""
    texto = "\n".join(ls)
    it.facturas = [x.strip() for x in _busca(r"Factura:\s*(.+)", texto, defecto="").split(";") if x.strip()]
    it.doc_carga = _busca(r"Doc\.Carga:\s*(\d+)", texto, defecto="")
    m = re.search(r"Subrégimen:\s*(\w+)\s+(.+?)\s+Cant\.Análisis", texto)
    if m:
        it.subregimen, it.subregimen_texto = m.group(1), m.group(2)
    it.origen = _busca(r"Origen:\s*(\d+\s+.+?)\s+Procedencia", texto, defecto="")
    it.procedencia = _busca(r"Procedencia:\s*(\d+\s+.+?)\s+Adquisición", texto, defecto="")
    it.adquisicion = _busca(r"Adquisición:\s*(\d+\s+.+)", texto, defecto="")
    m = re.search(r"Vol\. Físico:\s*(\d+\s+\w+)\s+(" + NUM + r")\s+Cant\. Comercial:\s*(\d+\s+\w+)\s+(" + NUM + ")", texto)
    if m:
        it.unidad_fisica, it.cant_fisica = m.group(1), num(m.group(2))
        it.unidad_comercial, it.cant_comercial = m.group(3), num(m.group(4))
    it.cif = _busca(r"CIF U\$S:\s*(" + NUM + ")", texto, conv=num, defecto=0.0)
    it.gastos = _busca(r"Gtos U\$S:\s*(" + NUM + ")", texto, conv=num, defecto=0.0)
    it.valor_dna = _busca(r"Valor DNA \$:\s*(" + NUM + ")", texto, conv=num, defecto=0.0)
    it.valor_dna_aj = _busca(r"Valor DNA Aj\.\$:\s*(" + NUM + ")", texto, conv=num, defecto=0.0)
    it.metodo = _busca(r"Método Valor:\s*(\d+)", texto, defecto="")
    it.forma_pago = _busca(r"F\.Pago:\s*(\d+)", texto, defecto="")
    it.descripcion = _busca(r"Descripción:\s*(.+)", texto, defecto="")
    it.refs = _referencias(ls)
    it.estado = _busca(r"Estado:\s*(\d+\s+[A-Z ]+)", texto, defecto="")
    m = re.search(r"Autoliq\.-->(.*)", texto)
    if m:
        for nombre, valor in re.findall(r"([A-Za-z. ]+?):\s*(" + NUM + ")%", m.group(1)):
            it.tasas[nombre.strip()] = num(valor)
    m = re.search(r"Autoliq\.-->.*\n\s*T\.Cons\.:\s*(" + NUM + ")%", texto)
    if m:
        it.tasas["T.Cons."] = num(m.group(1))
    for m in re.finditer(r"Cert:\s*(\w+).*?Nro:\s*(\S+)", texto):
        it.certificados.append((m.group(1), m.group(2)))
    return it


def _referencias(ls):
    """Junta la línea 'It:...' con sus continuaciones hasta el '.-' final."""
    partes = []
    for l in ls:
        if partes:
            if re.match(r"[A-Z][a-zé.]+\s*:", l) or l.startswith(("Autoliq", "Marcas", "Aviso", "Cert")):
                break
            partes.append(l)
        elif l.startswith("It:"):
            partes.append(l)
        if partes and partes[-1].rstrip().endswith(".-"):
            break
    texto = " ".join(partes)
    return re.sub(r"\s*,\s*", ",", texto)


def referencias_por_factura(item):
    """'It:5 al 6;8/Fac. 976; It: 13/Fac. 977' -> {'976': {5,6,8}, '977': {13}}"""
    res = {}
    texto = item.refs.replace("It: ", "It:")
    for bloque in re.findall(r"It:\s*([^/]+)/Fac\.\s*(\S+?)\s*(?:[;,]|\.-|$)", texto):
        rangos, fac = bloque
        nums = set()
        for parte in re.split(r"[;,]", rangos):
            parte = parte.strip()
            m = re.match(r"(\d+)\s*al\s*(\d+)", parte)
            if m:
                nums.update(range(int(m.group(1)), int(m.group(2)) + 1))
            elif parte.isdigit():
                nums.add(int(parte))
        res.setdefault(fac, set()).update(nums)
    if not res:
        # caso de una sola factura: "It:1.-" o "It:1 al 3.-"
        m = re.match(r"It:\s*([\d al;,]+)\.-", texto.strip())
        if m and len(item.facturas) == 1:
            nums = set()
            for parte in re.split(r"[;,]", m.group(1)):
                mm = re.match(r"(\d+)\s*al\s*(\d+)", parte.strip())
                if mm:
                    nums.update(range(int(mm.group(1)), int(mm.group(2)) + 1))
                elif parte.strip().isdigit():
                    nums.add(int(parte.strip()))
            res[item.facturas[0]] = nums
    return res
