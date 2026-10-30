"""Reglas del libro de criterios. Cada regla devuelve controles con su resultado.

Resultados posibles:
  error       dato casi seguro mal, con impacto en tributos o en la validez del DUA
  advertencia dato que no coincide con los documentos
  revisar     puede estar bien; conviene que lo mire una persona
  ok          control hecho sin observaciones
Los números de regla son los del libro de criterios (docs/libro_de_criterios.md).
"""
import re
from dataclasses import dataclass

from .borrador import referencias_por_factura
from .documentos import ocr_profundo
from .numeros import aparece, coincidencia_codigo, digito_contenedor, fmt, num, num_libre, solo_alnum

USD = "840"
MERCOSUR = {"032", "076", "600", "858"}
INCOTERMS = ["EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP"]
SEVERIDAD = {"error": 0, "advertencia": 1, "revisar": 2, "ok": 3}


@dataclass
class Control:
    regla: int
    tema: str
    resultado: str
    titulo: str
    detalle: str = ""


class Revision:
    def __init__(self, borrador, documentos=()):
        self.b = borrador
        self.docs = list(documentos)
        self.controles = []

    # -- utilidades --------------------------------------------------------
    def agregar(self, regla, tema, resultado, titulo, detalle=""):
        self.controles.append(Control(regla, tema, resultado, titulo, detalle))

    def textos(self, *tipos):
        return "\n".join(d.texto for d in self.vigentes() if not tipos or d.tipo in tipos)

    def vigentes(self):
        """Documentos a usar: si hay un borrador (draft) y la versión final del mismo tipo, se ignora el draft."""
        es_draft = lambda d: re.search(r"draft|borrador", d.ruta.stem, re.I) is not None
        finales = {d.tipo for d in self.docs if not es_draft(d)}
        return [d for d in self.docs if not (es_draft(d) and d.tipo in finales)]

    def hay(self, *tipos):
        return any(d.tipo in tipos and d.texto.strip() for d in self.docs)

    def a_usd(self, importe, moneda):
        if moneda in ("", USD) or not importe:
            return importe
        if moneda == self.b.moneda:
            return importe * self.b.arbitraje
        return None

    @staticmethod
    def cerca(a, b, abs_tol=1.0, rel_tol=0.0005):
        return abs(a - b) <= max(abs_tol, rel_tol * max(abs(a), abs(b)))

    def _doc_de_factura(self, f):
        """Documento que contiene la factura f: por número en el texto y, de desempate, en el nombre del archivo."""
        mejor, puntos_mejor = None, 0
        grupos = re.findall(r"\d+", f.numero)
        for d in self.docs:
            if not d.texto:
                continue
            puntos = 0
            if buscar_codigo(f.numero, d.texto):
                puntos += 2
            nombre = re.sub(r"\D+", " ", d.ruta.stem).split()
            if any(len(n) >= 2 and any(g == n or g.endswith(n) or n.endswith(g) and len(g) >= 3 for g in grupos)
                   for n in nombre if n != self.b.carpeta):
                puntos += 1
            if not puntos:
                continue
            if d.tipo == "factura":
                puntos += 2.5
            elif d.tipo != "packing":
                puntos -= 0.5
            if puntos >= 2 and puntos > puntos_mejor:
                mejor, puntos_mejor = d, puntos
        return mejor

    # -- ejecución ---------------------------------------------------------
    def ejecutar(self):
        for nombre in sorted(dir(self)):
            if nombre.startswith("r_"):
                try:
                    getattr(self, nombre)()
                except Exception as e:  # una regla rota no debe frenar las demás
                    self.agregar(0, "Programa", "revisar", f"No se pudo aplicar {nombre}", str(e))
        self.controles.sort(key=lambda c: (SEVERIDAD[c.resultado], c.regla))
        return self.controles

    # ======================================================================
    # Prioridades (reglas 1 a 11)
    # ======================================================================
    def r_01_importador(self):
        if not self.hay("factura"):
            return
        texto = self.textos("factura", "packing")
        rut = self.b.importador_rut
        palabras = [p for p in re.split(r"\W+", self.b.importador.upper()) if len(p) >= 4 and p not in ("S.A.", "SOCIEDAD", "ANONIMA", "LTDA")]
        if (rut and (rut in solo_alnum(texto) or rut[:9] in solo_alnum(texto))) or any(p in texto.upper() for p in palabras[:2]):
            self.agregar(1, "Importador", "ok", f"El importador {self.b.importador} figura en las facturas")
        else:
            self.agregar(1, "Importador", "advertencia", "No encontré al importador en las facturas",
                         f"Busqué el RUT {rut} y el nombre {self.b.importador}. Confirmar que las facturas estén a nombre del importador.")

    def r_02_incoterm(self):
        for f in self.b.facturas_comerciales:
            if f.cond_vta and self.b.cond_vta and f.cond_vta != self.b.cond_vta:
                self.agregar(2, "Incoterm", "error", f"Factura {f.numero}: condición {f.cond_vta} distinta del cabezal ({self.b.cond_vta})")
        for f in self.b.facturas_comerciales:
            doc = self._doc_de_factura(f)
            if not doc:
                continue
            t = " " + re.sub(r"[^A-Z]+", " ", doc.texto.upper()) + " "
            t = t.replace(" EX WORKS ", " EXW ").replace(" EXWORKS ", " EXW ").replace(" EXWORK ", " EXW ")
            en_doc = [i for i in INCOTERMS if f" {i} " in t]
            if not en_doc:
                continue
            if f.cond_vta in en_doc:
                self.agregar(2, "Incoterm", "ok", f"Factura {f.numero}: Incoterm {f.cond_vta} coincide")
            else:
                self.agregar(2, "Incoterm", "advertencia", f"Factura {f.numero}: declarada {f.cond_vta}, el documento dice {', '.join(en_doc)}",
                             f"Documento: {doc.nombre}. Si es EXW o FCA, el flete tiene que cubrir desde el punto de entrega (regla 19).")

    def r_03_frontera(self):
        if self.b.aduana and self.b.aduana_ingreso:
            a = int(self.b.aduana.split()[0])
            i = int(self.b.aduana_ingreso.split()[0])
            if a != i:
                self.agregar(3, "Frontera", "revisar", f"Aduana {self.b.aduana} y aduana de ingreso {self.b.aduana_ingreso} distintas",
                             "Confirmar que sea un tránsito entre aduanas.")
        if self.hay("transporte", "flete"):
            nombre = re.sub(r"^\d+\s+", "", self.b.aduana_ingreso).strip()
            if nombre and nombre.split()[0] in self.textos("transporte", "flete").upper():
                self.agregar(3, "Frontera", "ok", f"La aduana de ingreso ({nombre}) figura en el documento de transporte o flete")

    def r_04_subregimen(self):
        a = [it.nro for it in self.b.items if it.doc_carga == "32" and it.subregimen not in ("12", "M1", "M2")]
        if a:
            self.agregar(4, "Subrégimen", "advertencia", "Mercadería en depósito (Doc.Carga 32) sin subrégimen 12", "Ítems " + _rangos(a))
        a = [it.nro for it in self.b.items if it.doc_carga == "00" and it.subregimen == "12"]
        if a:
            self.agregar(4, "Subrégimen", "revisar", "Subrégimen 12 (desde depósito) con Doc.Carga 00", "Ítems " + _rangos(a) + ". Confirmar si corresponde declarar la entrada a depósito.")

    def r_05_moneda(self):
        for f in self.b.facturas_comerciales:
            if f.moneda and self.b.moneda and f.moneda != self.b.moneda:
                self.agregar(5, "Moneda", "error", f"Factura {f.numero} en moneda {f.moneda} y el cabezal en {self.b.moneda}")
            doc = self._doc_de_factura(f)
            if not doc:
                continue
            t = doc.texto.upper()
            eur = bool(re.search(r"\bEUR\b|€|\bEUROS?\b", t))
            usd = bool(re.search(r"\bUSD\b|US\$|U\$S|DOLAR|DOLLAR", t))
            if f.moneda == "902" and usd and not eur:
                self.agregar(5, "Moneda", "error", f"Factura {f.numero} declarada en euros y el documento está en dólares")
            elif f.moneda == USD and eur and not usd:
                self.agregar(5, "Moneda", "error", f"Factura {f.numero} declarada en dólares y el documento está en euros")

    def r_06_facturas(self):
        b = self.b
        tot = sum(f.valor for f in b.facturas_comerciales)
        if b.facturas_comerciales and not self.cerca(tot, b.tot_fac, 0.02, 0):
            self.agregar(12, "Valores", "error", "La suma de las facturas no da el Tot.Fac del cabezal",
                         f"Suma {fmt(tot)}, cabezal {fmt(b.tot_fac)}")
        if b.cant_fac and b.cant_fac != len(b.facturas_comerciales):
            self.agregar(6, "Facturas", "advertencia", f"Cant.Fac dice {b.cant_fac} y hay {len(b.facturas_comerciales)} facturas comerciales")
        # números de ítem de cada factura asignados una sola vez
        usados = {}
        for it in b.items:
            for fac, nros in referencias_por_factura(it).items():
                for n in nros:
                    usados.setdefault(fac, []).append((n, it.nro))
            for fac in it.facturas:
                if fac not in [f.numero for f in b.facturas_comerciales]:
                    self.agregar(6, "Facturas", "error", f"Ítem {it.nro} cita la factura {fac}, que no está en el cabezal")
        for f in b.facturas_comerciales:
            lista = usados.get(f.numero, [])
            nros = [n for n, _ in lista]
            faltan = sorted(set(range(1, f.cant_items + 1)) - set(nros)) if f.cant_items else []
            repetidos = sorted({n for n in nros if nros.count(n) > 1})
            if faltan:
                self.agregar(7, "Cantidades", "error", f"Factura {f.numero}: líneas sin asignar a ningún ítem del DUA", "Líneas " + _rangos(faltan))
            if repetidos:
                self.agregar(7, "Cantidades", "error", f"Factura {f.numero}: líneas asignadas a más de un ítem", "Líneas " + _rangos(repetidos))
            if f.cant_items and not faltan and not repetidos:
                self.agregar(7, "Cantidades", "ok", f"Factura {f.numero}: las {f.cant_items} líneas están asignadas una vez")
        # fechas
        fdua = _fecha(b.fecha)
        for f in b.facturas:
            ff = _fecha(f.fecha)
            if fdua and ff and ff > fdua:
                self.agregar(26, "Fechas", "advertencia", f"Factura {f.numero} ({f.fecha}) posterior a la fecha del borrador ({b.fecha})")
            if fdua and ff and (fdua[0] - ff[0]) > 1:
                self.agregar(26, "Fechas", "revisar", f"Factura {f.numero} con fecha {f.fecha}, muy anterior al despacho")
        # cada factura en los documentos
        if not self.hay("factura"):
            if b.facturas_comerciales:
                self.agregar(6, "Facturas", "revisar", "No hay documentos de factura en la carpeta (o no se pudieron leer)")
            return
        sin_doc, sin_importe, sin_fecha = [], [], []
        for f in b.facturas_comerciales:
            doc = self._doc_de_factura(f)
            if not doc:
                sin_doc.append(f.numero)
                continue
            if aparece(f.valor, doc.texto):
                self.agregar(6, "Facturas", "ok", f"Factura {f.numero}: importe {fmt(f.valor)} encontrado en {doc.nombre}")
            else:
                sin_importe.append(f"{f.numero} ({fmt(f.valor)})")
            fecha = _fecha(f.fecha)
            if fecha and _fecha_en_texto(fecha, doc.texto):
                self.agregar(6, "Fechas", "ok", f"Factura {f.numero}: fecha {f.fecha} encontrada")
            elif fecha:
                sin_fecha.append(f"{f.numero} ({f.fecha})")
        if sin_doc:
            self.agregar(6, "Facturas", "revisar", "No encontré estas facturas en los documentos: " + ", ".join(sin_doc),
                         "Puede ser el OCR. Confirmar los números de factura.")
        if sin_importe or sin_fecha:
            detalle = []
            if sin_importe:
                detalle.append("Importe: " + ", ".join(sin_importe))
            if sin_fecha:
                detalle.append("Fecha: " + ", ".join(sin_fecha))
            self.agregar(6, "Facturas", "revisar", "No pude confirmar con el OCR el importe o la fecha de algunas facturas",
                         ". ".join(detalle) + ". Revisarlos a ojo.")

    def r_08_pesos(self):
        b = self.b
        neto = sum(i.neto for i in b.items)
        bruto = sum(i.bruto for i in b.items)
        bultos = sum(i.bultos for i in b.items)
        if not self.cerca(neto, b.peso_neto, 0.01, 0):
            self.agregar(8, "Pesos", "error", "La suma de netos de los ítems no da el total", f"{fmt(neto, 3)} vs {fmt(b.peso_neto, 3)}")
        if not self.cerca(bruto, b.peso_bruto, 0.01, 0):
            self.agregar(8, "Pesos", "error", "La suma de brutos de los ítems no da el total", f"{fmt(bruto, 3)} vs {fmt(b.peso_bruto, 3)}")
        if not self.cerca(bultos, b.total_bultos, 0.01, 0):
            self.agregar(28, "Bultos", "error", "La suma de bultos de los ítems no da el total", f"{fmt(bultos, 0)} vs {fmt(b.total_bultos, 0)}")
        for it in b.items:
            if it.neto > it.bruto + 0.001:
                self.agregar(8, "Pesos", "error", f"Ítem {it.nro}: neto mayor que bruto", f"{fmt(it.neto, 3)} > {fmt(it.bruto, 3)}")
        if b.peso_neto > b.peso_bruto:
            self.agregar(8, "Pesos", "error", "Peso neto total mayor que el bruto")
        if b.peso_neto and abs(b.peso_neto * 1000 - round(b.peso_neto * 1000)) < 1e-6 and re.search(r"\.99\d$|999$", f"{b.peso_neto:.3f}"):
            self.agregar(29, "Pesos", "revisar", f"El neto {fmt(b.peso_neto, 3)} parece estimado", "Confirmar contra el packing.")
        docs_peso = self.textos("transporte", "packing", "flete") or self.textos("factura", "origen")
        if docs_peso.strip():
            if aparece(b.peso_bruto, docs_peso, 3) or aparece(b.peso_bruto, docs_peso, 2) or aparece(round(b.peso_bruto), docs_peso, 0):
                self.agregar(8, "Pesos", "ok", f"Peso bruto {fmt(b.peso_bruto, 3)} encontrado en transporte/packing")
            else:
                lb = b.peso_bruto / 0.45359237
                self.agregar(8, "Pesos", "advertencia", f"No encontré el peso bruto {fmt(b.peso_bruto, 3)} kg en los documentos",
                             f"Si el documento está en libras serían {fmt(lb, 0)} lb (regla 30).")
            if aparece(b.total_bultos, docs_peso, 0):
                self.agregar(28, "Bultos", "ok", f"Los {fmt(b.total_bultos, 0)} bultos figuran en transporte/packing")
            elif b.total_bultos and round(b.total_bultos) in {_suma_bultos_por_contenedor(d.texto) for d in self.vigentes() if d.tipo == "transporte"}:
                self.agregar(28, "Bultos", "ok", f"Los {fmt(b.total_bultos, 0)} bultos coinciden con la suma por contenedor del BL")
            else:
                self.agregar(28, "Bultos", "advertencia", f"No encontré {fmt(b.total_bultos, 0)} bultos en el documento de transporte ni en el packing")
        if self.hay("packing"):
            pk = self.textos("packing")
            docs_neto = [d for d in self.vigentes() if d.tipo in ("packing", "factura")]
            netos = [n for _, n in filter(None, (_bruto_neto_kg(d.texto) for d in docs_neto))]
            if aparece(b.peso_neto, pk, 3) or aparece(b.peso_neto, pk, 2):
                self.agregar(29, "Pesos", "ok", f"Peso neto {fmt(b.peso_neto, 3)} coincide con el packing")
            elif len(netos) > 1 and self.cerca(sum(netos), b.peso_neto, 0.5, 0):
                self.agregar(29, "Pesos", "ok", f"Peso neto {fmt(b.peso_neto, 3)} coincide con la suma de los netos de {len(netos)} documentos")
                return
            elif not re.search(r"\bNET(?:T|TO|O)?\s*(?:WEIGHT|WT|GEW|KG)|\bN\.\s?W\b|NETGEW|NETTOGEW|PESO NETO|PESO L[IÍ]Q", pk.upper()):
                self.agregar(29, "Pesos", "revisar", "El packing no trae peso neto: confirmar de dónde sale el neto declarado")
                return
            else:
                self.agregar(29, "Pesos", "advertencia", f"No encontré el peso neto {fmt(b.peso_neto, 3)} en el packing")
            sin = [it.nro for it in b.items if it.neto and not (aparece(it.neto, pk, 3) or aparece(it.neto, pk, 2))]
            if len(sin) > len(b.items) / 2 and len(b.items) > 2:
                self.agregar(29, "Pesos", "revisar", "Los netos por ítem no salen del packing (¿prorrateados?)",
                             f"{len(sin)} de {len(b.items)} ítems con un neto que no figura en el packing. Si el packing trae peso por línea, usarlo.")

    def r_09_unidades(self):
        b = self.b
        total = sum(i.cant_comercial for i in b.items)
        if b.total_unidades and not self.cerca(total, b.total_unidades, 0.01, 0):
            self.agregar(9, "Unidades", "advertencia", "La suma de cantidades comerciales no da el Total de Unidades", f"{fmt(total, 3)} vs {fmt(b.total_unidades, 3)}")
        for it in b.items:
            uf = it.unidad_fisica.split()[-1] if it.unidad_fisica else ""
            if uf.startswith("KILO") and it.neto and not self.cerca(it.cant_fisica, it.neto, 0.01, 0.001):
                self.agregar(9, "Unidades", "advertencia", f"Ítem {it.nro}: volumen físico en kilos ({fmt(it.cant_fisica, 3)}) distinto del neto ({fmt(it.neto, 3)})")
            uc = it.unidad_comercial.split()[-1] if it.unidad_comercial else ""
            if uc.startswith("KILO") and it.neto and not self.cerca(it.cant_comercial, it.neto, 0.01, 0.001):
                self.agregar(9, "Unidades", "revisar", f"Ítem {it.nro}: cantidad comercial en kilos ({fmt(it.cant_comercial, 3)}) distinta del neto")
            if uc.startswith("TONEL") and it.neto and not self.cerca(it.cant_comercial * 1000, it.neto, 1, 0.001):
                self.agregar(9, "Unidades", "advertencia", f"Ítem {it.nro}: {fmt(it.cant_comercial, 3)} toneladas no coincide con {fmt(it.neto, 3)} kg netos")

    def r_09_cantidades_en_factura(self):
        """En despachos chicos, la cantidad comercial de cada ítem tiene que estar en su factura."""
        b = self.b
        if not b.items or len(b.items) > 5:
            return
        facs = {f.numero: f for f in b.facturas_comerciales}
        errores, dudas = [], []
        for it in b.items:
            refs = referencias_por_factura(it)
            if len(it.facturas) != 1 or it.facturas[0] not in facs or sum(len(v) for v in refs.values()) > 1:
                continue  # si el ítem junta varias líneas, la cantidad es una suma
            f = facs[it.facturas[0]]
            doc = self._doc_de_factura(f)
            if not doc or not it.cant_comercial or aparece(it.cant_comercial, doc.texto, 0):
                continue
            desc = f"ítem {it.nro}: declarado {fmt(it.cant_comercial, 0)}"
            texto = doc.texto
            if doc.ocr:
                try:
                    texto += "\n" + ocr_profundo(doc.ruta)
                except Exception:
                    pass
                if aparece(it.cant_comercial, texto, 0):
                    continue
            # con una sola línea en la factura, cantidad x precio tiene que dar el importe
            leida = _cantidad_por_precio(texto, f.valor) if f.cant_items == 1 else None
            if leida:
                errores.append(f"{desc}, la factura dice {fmt(leida[0], 0)} × {fmt(leida[1])} = {fmt(f.valor)}")
            elif not doc.ocr:
                errores.append(desc)
            else:
                dudas.append(desc)
        if errores:
            self.agregar(9, "Cantidades", "error", "La cantidad declarada no coincide con la factura", "; ".join(errores))
        if dudas:
            self.agregar(9, "Cantidades", "revisar", "No pude leer en la factura la cantidad declarada", "; ".join(dudas) + ". Confirmar a ojo.")

    def r_10_origen(self):
        declarados = {_pais(it.origen) for it in self.b.items if it.origen}
        texto = self.textos("packing", "factura")
        if not texto.strip():
            return
        encontrados = {}
        for linea in texto.upper().splitlines():
            if re.search(r"\bAV\b|AVDA|AVENIDA|CALLE|STREET|ROAD|\bRUT\b|ADDRESS|DIRECCI|TEL|FAX|\bCP\b|C\.P\.|PORT|PUERTO|DESTINO|DESTINATION|CONSIGNEE|SHIPPER", linea):
                continue
            hay_contexto = re.search(r"ORIG|MADE IN|PROCEDEN", linea)
            for pais, claves in PAISES.items():
                for k in claves:
                    if re.search(r"\b" + k + r"\b", linea):
                        if hay_contexto or self._es_tabla_origen(linea):
                            encontrados.setdefault(pais, linea.strip()[:90])
        otros = {p: l for p, l in encontrados.items() if p not in declarados}
        if otros:
            for p, l in otros.items():
                self.agregar(10, "Origen", "error", f"Los documentos indican origen {p} y el DUA no lo declara",
                             f"Declarado: {', '.join(sorted(declarados))}. Línea: «{l}». El origen se declara aunque no haya certificado (regla 31).")
        elif encontrados:
            self.agregar(10, "Origen", "ok", f"Orígenes de los documentos coinciden con el DUA ({', '.join(sorted(declarados))})")

    def _es_tabla_origen(self, linea):
        # línea de packing con código, descripción y país al final (formato tabla)
        return bool(re.search(r"\d", linea)) and len(linea) > 30 and re.search(r"[|\]]\s*\w+\s*[|\]]?\s*$", linea) is not None

    def r_11_bajo_arancel(self):
        for it in self.b.items:
            if it.subregimen == "M1":
                continue
            rec, ima = it.tasas.get("Recargo"), it.tasas.get("IMADUNI")
            if rec is None or ima is None:
                continue
            if rec + ima == 0 and it.origen[:3] not in MERCOSUR:
                self.agregar(11, "Clasificación", "revisar", f"Ítem {it.nro} ({it.ncm}): partida con 0% de recargo e IMADUNI",
                             f"«{it.descripcion}». Confirmar que la descripción justifique esta partida.")
            elif rec + ima <= 5 and it.origen[:3] not in MERCOSUR and any(
                    o.tasas.get("Recargo", 0) + o.tasas.get("IMADUNI", 0) >= 10 for o in self.b.items if o is not it):
                self.agregar(11, "Clasificación", "revisar", f"Ítem {it.nro} ({it.ncm}): arancel bajo ({fmt(rec + ima)}%) frente al resto del despacho",
                             f"«{it.descripcion}». Confirmar la clasificación.")

    # ======================================================================
    # Valores y tributos (reglas 12 a 20)
    # ======================================================================
    def r_13_cif(self):
        b = self.b
        cif_items = sum(i.cif for i in b.items)
        gtos_items = sum(i.gastos for i in b.items)
        if not self.cerca(cif_items, b.cif, 0.05, 0):
            self.agregar(13, "Valores", "error", "La suma del CIF de los ítems no da el CIF del cabezal", f"{fmt(cif_items)} vs {fmt(b.cif)}")
        if not self.cerca(gtos_items, b.gastos, 0.05, 0):
            self.agregar(15, "Valores", "error", "Gastos/Aj del cabezal distinto de la suma de gastos de los ítems",
                         f"Cabezal {fmt(b.gastos)}, ítems {fmt(gtos_items)}. El VNA y el Valor DNA de los ítems usan {fmt(b.gastos)}: "
                         f"diferencia {fmt(gtos_items - b.gastos)} USD.")
        if not self.cerca(b.cif + b.gastos, b.vna, 0.05, 0):
            self.agregar(13, "Valores", "error", "VNA distinto de CIF + gastos", f"{fmt(b.cif)} + {fmt(b.gastos)} ≠ {fmt(b.vna)}")
        if b.tc and not self.cerca(b.vna * b.tc, b.vna_pesos, 5, 0.0002):
            self.agregar(14, "Valores", "error", "VNA en pesos distinto de VNA × T/C", f"{fmt(b.vna)} × {b.tc} = {fmt(b.vna * b.tc)} vs {fmt(b.vna_pesos)}")
        # CIF esperado desde las facturas
        esperado, faltan = 0.0, False
        for f in b.facturas_comerciales:
            partes = [self.a_usd(f.valor, f.moneda), self.a_usd(f.flete, f.flete_moneda or USD), self.a_usd(f.seguro, f.seguro_moneda or USD)]
            if None in partes:
                faltan = True
                break
            esperado += sum(partes)
        if b.facturas_comerciales and not faltan:
            if self.cerca(esperado, b.cif, 1.0, 0.0002):
                self.agregar(13, "Valores", "ok", f"CIF {fmt(b.cif)} = FOB + flete + seguro" + (f" (arbitraje {b.arbitraje})" if b.moneda != USD else ""))
            else:
                detalle = f"Calculado {fmt(esperado)} USD, declarado {fmt(b.cif)} USD (diferencia {fmt(esperado - b.cif)})."
                if b.moneda != USD:
                    fob = sum(f.valor for f in b.facturas_comerciales)
                    resto = b.cif - sum(self.a_usd(f.flete, f.flete_moneda or USD) + self.a_usd(f.seguro, f.seguro_moneda or USD) for f in b.facturas_comerciales)
                    if fob:
                        detalle += f" El CIF declarado corresponde a un arbitraje de {resto / fob:.4f} y el cabezal dice {b.arbitraje}."
                self.agregar(14, "Valores", "error", "El CIF no cierra con facturas, flete, seguro y arbitraje", detalle)
        malos = [it.nro for it in b.items if b.tc and it.valor_dna and not self.cerca((it.cif + it.gastos) * b.tc, it.valor_dna, 2, 0.0002)]
        if malos and self.cerca(gtos_items, b.gastos, 0.05, 0):
            self.agregar(14, "Valores", "error", "Valor DNA de ítems distinto de (CIF + gastos) × T/C", "Ítems " + _rangos(malos))
        suma = sum(it.valor_dna for it in b.items)
        if b.items and suma and not self.cerca(suma, b.vna_pesos, 2 + len(b.items), 0.0002):
            self.agregar(14, "Valores", "error", "La suma del Valor DNA de los ítems no da el VNA en pesos", f"{fmt(suma)} vs {fmt(b.vna_pesos)}")

    def r_13_tipo_de_cambio(self):
        tc = self.b.tc
        if tc and abs(tc * 100 - round(tc * 100)) < 1e-6 and abs(tc - round(tc, 1)) < 1e-6:
            self.agregar(13, "Valores", "revisar", f"T/C {fmt(tc, 4)} parece cargado a mano",
                         "El tipo de cambio del BCU suele tener cuatro decimales. Confirmar el del día.")

    def r_15_gastos(self):
        b = self.b
        if not b.gastos and not any(i.gastos for i in b.items):
            return
        t = self.textos("gastos").upper()
        if any(re.search(r"ALMACENAJE|STORAGE", l) and re.search(r"\d+[.,]\d{2}", l) for l in t.splitlines()):
            self.agregar(16, "Gastos", "revisar", "Hay almacenaje en los documentos de gastos",
                         "El almacenaje no va en Gastos/Aj. Confirmar que no esté sumado.")
        no_gravado = [num_libre(m) for m in re.findall(r"NO\s+GRAVAD[OA]S?\D{0,15}?(\d[\d.,]*\d)", t)]
        no_gravado = [x for x in no_gravado if x]
        if no_gravado:
            total = round(sum(no_gravado), 2)
            if self.cerca(total, b.gastos, 0.01, 0):
                self.agregar(16, "Gastos", "ok", f"Gastos/Aj {fmt(b.gastos)} = subtotal no gravado de la factura de gastos")
            else:
                self.agregar(16, "Gastos", "revisar", f"Gastos/Aj {fmt(b.gastos)} USD y el subtotal no gravado de gastos es {fmt(total)}",
                             f"Diferencia {fmt(b.gastos - total)}. Los gastos que van al valor son los que no llevan IVA (salvo almacenaje).")
            return
        facs = ", ".join(f"{f.numero} ({fmt(f.valor)})" for f in b.facturas_flete)
        self.agregar(16, "Gastos", "revisar", f"Gastos/Aj de {fmt(b.gastos)} USD: confirmar que no lleven IVA ni sean almacenaje",
                     ("Facturas de flete y gastos del cabezal: " + facs) if facs else "No hay facturas de gastos (FACC) en el cabezal.")

    def r_17_prorrateo(self):
        facs = [f for f in self.b.facturas_comerciales if f.valor]
        if len(facs) < 2:
            return
        for campo, regla in (("flete", 17), ("seguro", 18)):
            factores = []
            for f in facs:
                v = self.a_usd(f.valor, f.moneda)
                x = self.a_usd(getattr(f, campo), getattr(f, campo + "_moneda") or USD)
                if v and x is not None:
                    factores.append((f.numero, x / v))
            if len(factores) < 2:
                continue
            base = sorted(x for _, x in factores)[len(factores) // 2]
            raros = [(n, x) for n, x in factores if base and abs(x - base) / base > 0.03 and abs(x - base) * 1000 > 0.5]
            if raros:
                self.agregar(regla, "Valores", "advertencia", f"El {campo} no está prorrateado parejo por FOB",
                             "; ".join(f"{n}: {x * 100:.3f}%" for n, x in raros) + f" (el resto {base * 100:.3f}%)")

    def r_18_seguro(self):
        facs = self.b.facturas_comerciales
        if not facs:
            return
        ficto = all(f.valor and self.cerca(self.a_usd(f.seguro, f.seguro_moneda or USD) or 0, (self.a_usd(f.valor, f.moneda) or 0) * 0.01, 0.02, 0.002)
                    or (f.seguro_moneda == f.moneda and self.cerca(f.seguro, f.valor * 0.01, 0.02, 0.002)) for f in facs)
        total = sum(f.seguro for f in facs)
        if not total and all(f.cond_vta in ("CIF", "CIP", "DAP", "DPU", "DDP", "CFR", "CPT") for f in facs):
            self.agregar(18, "Seguro", "ok", "Seguro 0: va incluido en el precio de la factura por la condición de venta")
            return
        if ficto:
            if self.hay("seguro"):
                self.agregar(18, "Seguro", "advertencia", "Se usó seguro ficto 1% pero hay certificado de seguro en la carpeta",
                             "Con certificado corresponde el premio internacional del certificado.")
            else:
                self.agregar(18, "Seguro", "ok", "Seguro ficto 1% (no hay certificado en la carpeta)")
        elif self.hay("seguro"):
            if aparece(total, self.textos("seguro")):
                self.agregar(18, "Seguro", "ok", f"Seguro {fmt(total)} encontrado en el certificado")
            else:
                self.agregar(18, "Seguro", "revisar", f"No encontré el seguro {fmt(total)} en el certificado", "Puede ser un error del OCR.")
        else:
            self.agregar(18, "Seguro", "revisar", f"Seguro {fmt(total)} sin certificado en la carpeta ni ficto 1%")

    def r_19_flete(self):
        facs = self.b.facturas_comerciales
        total = round(sum(self.a_usd(f.flete, f.flete_moneda or USD) or 0 for f in facs), 2)
        if not total:
            if facs and all(f.cond_vta in ("FOB", "FCA", "EXW", "FAS") for f in facs):
                self.agregar(19, "Flete", "error", "Condición sin flete incluido y flete 0")
            return
        if not self.hay("flete", "transporte", "gastos"):
            self.agregar(19, "Flete", "revisar", f"Flete {fmt(total)} sin documento de flete en la carpeta")
            return
        if aparece(total, self.textos("flete", "transporte", "gastos")):
            self.agregar(19, "Flete", "ok", f"Flete {fmt(total)} encontrado en el documento de flete")
        else:
            self.agregar(19, "Flete", "revisar", f"No encontré el flete {fmt(total)} en los documentos de flete", "Puede ser un error del OCR o un flete compuesto.")

    def r_20_tributos(self):
        b = self.b
        if not b.autoliquidacion or not b.tc:
            return
        calc = {"Recargo": 0.0, "IMADUNI": 0.0, "I.V.A.Importación": 0.0, "I.V.A.Anticipado": 0.0, "Anticipo IRAE": 0.0, "Tasa Consular": 0.0}
        for it in b.items:
            base = it.valor_dna_aj or it.valor_dna
            t = it.tasas
            rec = base * t.get("Recargo", 0) / 100
            ima = base * t.get("IMADUNI", 0) / 100
            base_iva = base + rec + ima
            calc["Recargo"] += rec
            calc["IMADUNI"] += ima
            calc["I.V.A.Importación"] += base_iva * t.get("IVA", 0) / 100
            calc["I.V.A.Anticipado"] += base_iva * t.get("Ant. IVA", 0) / 100
            calc["Anticipo IRAE"] += base_iva * t.get("A.IRAE", 0) / 100
            calc["Tasa Consular"] += base * t.get("T.Cons.", 0) / 100
        malos = []
        for nombre, v in calc.items():
            decl = b.autoliquidacion.get(nombre)
            if decl is None:
                continue
            if not self.cerca(v, decl, 3 + len(b.items), 0.001):
                malos.append(f"{nombre}: calculado {fmt(v, 0)}, declarado {fmt(decl, 0)}")
        if malos:
            self.agregar(20, "Tributos", "error", "Tributos que no cierran con las tasas de los ítems", "; ".join(malos))
        else:
            self.agregar(20, "Tributos", "ok", "Recargo, IMADUNI, IVA, IVA anticipado, IRAE y tasa consular cierran")
        if b.liquidacion_dna:
            dif = [f"{k}: {fmt(v, 0)} vs DNA {fmt(b.liquidacion_dna[k], 0)}" for k, v in b.autoliquidacion.items()
                   if k in b.liquidacion_dna and abs(v - b.liquidacion_dna[k]) > max(5, abs(v) * 0.00002)]  # tolera el redondeo por ítem
            if dif:
                self.agregar(20, "Tributos", "revisar", "La autoliquidación difiere de la liquidación DNA", "; ".join(dif))

    # ======================================================================
    # Transporte (21 a 27)
    # ======================================================================
    def r_21_documento_transporte(self):
        t = self.textos("transporte")
        if not t.strip():
            if self.b.facturas_comerciales and not self.b.medio_transporte.startswith("7"):
                self.agregar(21, "Transporte", "revisar", "No hay documento de transporte (BL, guía o CRT) en la carpeta, o no se pudo leer")
            return
        if re.search(r"\bDRAFT\b|\bBORRADOR\b|NON[- ]NEGOTIABLE COPY", t.upper()) and "DRAFT" in t.upper():
            self.agregar(27, "Transporte", "error", "El documento de transporte es un DRAFT", "No numerar hasta tener el definitivo.")
        codigos = []
        for k in ("MAWB", "HAWB", "BL", "B/L", "CRT"):
            for m in re.finditer(k + r"\s*:?\s*([A-Z0-9-]{6,})", self.b.observaciones.upper()):
                codigos.append((k, m.group(1)))
        for it in self.b.items:
            for c, n in it.certificados:
                if c == "CONC":
                    codigos.append(("Conocimiento", n))
        todos = self.textos()
        for k, n in dict.fromkeys(codigos):
            c = coincidencia_codigo(n, todos)
            if c == "exacta":
                self.agregar(21, "Transporte", "ok", f"{k} {n} coincide con el documento de transporte")
            elif c == "aproximada":
                self.agregar(21, "Transporte", "revisar", f"{k} {n}: en el documento de transporte se lee parecido",
                             "Puede ser el OCR o un carácter mal cargado (por ejemplo S y 5). Confirmar.")
            else:
                otro = re.search(k + r"\s*(?:NO\.?|N°|#)?\s*:?\s*([A-Z0-9][A-Z0-9 -]{6,20}\d)", todos.upper())
                if otro and coincidencia_codigo(otro.group(1), n) is None:
                    self.agregar(21, "Transporte", "error", f"{k} {n} distinta de la del documento ({otro.group(1).strip()})",
                                 "Revisar el número cargado.")
                else:
                    self.agregar(21, "Transporte", "revisar", f"No encontré {k} {n} en los documentos", "Puede ser el OCR. Confirmar el número.")

    def r_22_buque(self):
        if not self.b.medio_transporte.startswith("1"):
            return
        t = self.textos("transporte", "flete").upper()
        nombre = re.sub(r"[^A-Z0-9 ]", " ", self.b.transportista.upper()).strip()
        if not t.strip() or not nombre:
            return
        if nombre in re.sub(r"[^A-Z0-9 ]", " ", t) or all(p in t for p in nombre.split()):
            self.agregar(22, "Transporte", "ok", f"Buque {nombre} coincide con el BL")
        else:
            self.agregar(22, "Transporte", "revisar", f"El buque {nombre} no figura en el BL",
                         "Está bien si es el buque de arribo (transbordo). Si no, corregir.")

    def r_23_contenedores(self):
        b = self.b
        if b.cant_contenedores != len(b.contenedores):
            self.agregar(23, "Contenedor", "advertencia", f"Cantidad de contenedores {b.cant_contenedores} y hay {len(b.contenedores)} declarados")
        t = self.textos("transporte")
        for nro, precinto, _ in b.contenedores:
            dv = digito_contenedor(nro)
            if dv and dv[0] != dv[1]:
                self.agregar(23, "Contenedor", "error", f"Contenedor {nro}: dígito verificador incorrecto (debería ser {dv[0]})")
            if precinto and not precinto.isdigit():
                self.agregar(24, "Precinto", "error", f"Precinto {precinto} con letras: se declara solo la parte numérica")
            if t.strip():
                for nombre, regla, valor in (("Contenedor", 23, nro), ("Precinto", 24, precinto)):
                    if not valor:
                        continue
                    c = coincidencia_codigo(valor, t)
                    if c == "exacta":
                        self.agregar(regla, nombre, "ok", f"{nombre} {valor} coincide con el BL")
                    elif c == "aproximada":
                        self.agregar(regla, nombre, "revisar", f"{nombre} {valor}: en el BL se lee parecido", "Puede ser el OCR. Confirmar contra el BL.")
                    else:
                        self.agregar(regla, nombre, "advertencia", f"El {nombre.lower()} {valor} no figura en el BL")
        if not b.contenedores and t.strip():
            en_bl = sorted({c for c in re.findall(r"\b([A-Z]{4})\s?-?\s?(\d{6})\s?-?\s?(\d)\b", t.upper()) for c in [("".join(c))]})
            validos = [c for c in en_bl if (lambda d: d and d[0] == d[1])(digito_contenedor(c))]
            if validos:
                lcl = re.search(r"\bLCL\b|\bCFS\b|CONSOLIDAD|GROUPAGE", t.upper())
                if lcl:
                    self.agregar(23, "Contenedor", "ok", f"Carga LCL: no se declara contenedor (el BL nombra {', '.join(validos)})")
                else:
                    self.agregar(23, "Contenedor", "error", f"El BL nombra el contenedor {', '.join(validos)} y el DUA no declara contenedores",
                                 "En carga FCL (contenedor lleno) el contenedor se declara. En LCL no.")

    # ======================================================================
    # Clasificación y subrégimen (31 a 37)
    # ======================================================================
    def r_31_certificado_origen(self):
        t = self.textos("origen")
        certs = [(it, n) for it in self.b.items for c, n in it.certificados if c.startswith("COD") or c.startswith("CERO")]
        if not certs:
            if t.strip():
                self.agregar(31, "Origen", "revisar", "Hay certificado de origen en la carpeta y ningún ítem lo cita")
            return
        if not t.strip():
            self.agregar(31, "Origen", "revisar", "El DUA cita certificado de origen y no está en la carpeta (o no se pudo leer)")
            return
        for it, n in certs:
            nro = re.sub(r"^VU", "", n)
            if coincidencia_codigo(nro, t):
                self.agregar(31, "Origen", "ok", f"Ítem {it.nro}: certificado de origen {n} coincide")
            else:
                self.agregar(31, "Origen", "advertencia", f"Ítem {it.nro}: no encontré el certificado {n} en el certificado de origen")
            ncm = re.sub(r"\D", "", it.ncm)[:8]
            if not re.search(r"\s?".join(ncm[:4]) + r"[.\s]?" + ncm[4:6] + r"[.\s]?" + ncm[6:8], t):
                self.agregar(31, "Origen", "advertencia", f"Ítem {it.nro}: el NCM {it.ncm} no figura en el certificado de origen",
                             "La preferencia aplica solo si el certificado ampara esa posición.")
        for f in self.b.facturas_comerciales:
            if not buscar_codigo(f.numero.split("/")[0], t):
                self.agregar(31, "Origen", "advertencia", f"El certificado de origen no cita la factura {f.numero}")

    def r_32_hs(self):
        t = self.textos("transporte", "factura").upper()
        if not t.strip():
            return
        hs = set()
        for m in re.finditer(r"H\.?\s?S\.?\s*(?:CODE|NO|N°|#)?\s*[:.]?\s*(\d{4})[.\s]?(\d{2})", t):
            hs.add(m.group(1) + m.group(2))
        if not hs:
            return
        declarados = {re.sub(r"\D", "", it.ncm)[:6] for it in self.b.items}
        for h in sorted(hs - declarados):
            parecidos = [d for d in declarados if d[:4] == h[:4]]
            self.agregar(32, "Clasificación", "revisar", f"Los documentos indican HS {h[:4]}.{h[4:]} y no hay ítem con esa subpartida",
                         ("Ítems en la misma partida: " + ", ".join(f"{d[:4]}.{d[4:]}" for d in parecidos)) if parecidos else "Confirmar la clasificación.")

    def r_33_ncm_incompleto(self):
        for it in self.b.items:
            if it.subregimen != "M1" and len(re.sub(r"\D", "", it.ncm)) < 10:
                self.agregar(32, "Clasificación", "advertencia", f"Ítem {it.nro}: NCM {it.ncm} incompleto")

    def r_36_muestras(self):
        b = self.b
        m1 = [it for it in b.items if it.subregimen == "M1" and (b.nat_transac == "10" or it.metodo == "01" or it.forma_pago in ("1", "2", "3"))]
        if m1:
            it = m1[0]
            self.agregar(36, "Subrégimen", "advertencia", "M1 (sin valor comercial) con compraventa, método 01 o pago",
                         f"Ítems {_rangos([i.nro for i in m1])}. Nat. transacción {b.nat_transac}, método {it.metodo}, forma de pago {it.forma_pago}. "
                         "M1 es solo para muestras inutilizadas para la venta.")
            if it.subregimen == "M2" and it.tasas and all(v == 0 for k, v in it.tasas.items() if k in ("Recargo", "IMADUNI", "IVA")):
                self.agregar(36, "Subrégimen", "revisar", f"Ítem {it.nro}: M2 (con valor comercial) con todos los tributos en 0%")
        t = self.textos("factura").upper()
        if re.search(r"SIN CAR[AÁ]CTER COMERCIAL|SIN VALOR COMERCIAL|NO COMMERCIAL VALUE|FREE OF CHARGE|N\.?C\.?V\.?\b", t):
            if not any(it.subregimen in ("M1", "M2") for it in b.items):
                self.agregar(36, "Subrégimen", "advertencia", "Hay una factura «sin carácter comercial» y ningún ítem va como muestra",
                             "Confirmar naturaleza de la transacción, método de valoración y forma de pago de esa factura.")

    def r_37_reparacion(self):
        # Solo facturas y descripciones: las condiciones impresas de los BL hablan de "repairs" y "warranty"
        t = (self.textos("factura") + " " + " ".join(i.descripcion for i in self.b.items)).upper()
        if re.search(r"\bREPAIR|\bREPARACI|\bREPARAD|\bWARRANTY|\bGARANT[IÍ]A", t):
            if any(it.subregimen in ("11", "12") for it in self.b.items):
                self.agregar(37, "Subrégimen", "error", "Los documentos hablan de reparación o garantía y el DUA es una importación definitiva común",
                             "Si el bien salió de Uruguay a reparar, corresponde reimportación tras exportación temporal citando esa salida. "
                             "Si es un reemplazo en garantía, revisar valor y naturaleza.")


# ---------------------------------------------------------------------------
def buscar_codigo(codigo, texto):
    """Busca un número de documento tolerando separadores y espacios del OCR, sin pegarse a otros dígitos."""
    c = solo_alnum(codigo)
    if not c:
        return False
    patron = r"(?<![A-Z0-9])" + r"[\s./_-]*".join(map(re.escape, c)) + r"(?![A-Z0-9])"
    return re.search(patron, texto.upper()) is not None


def _cantidad_por_precio(texto, importe):
    """Busca en el texto una cantidad entera y un precio unitario cuyo producto da el importe."""
    numeros = sorted({x for x in (num_libre(m) for m in re.findall(r"\d[\d.,]*\d|\d", texto)) if x})
    for q in numeros:
        if q != int(q) or q < 2 or q >= importe:
            continue
        for p in numeros:
            if p != q and p != 1 and abs(q * p - importe) <= 0.02:
                return q, p
    return None


def _bruto_neto_kg(texto):
    """Último renglón con dos pesos en kg ('465 kg  419 kg'), que suele ser el total bruto y neto del documento."""
    pares = re.findall(r"(\d[\d.,]*)\s*KGS?\s+(\d[\d.,]*)\s*KGS?\b", texto.upper())
    if not pares:
        return None
    bruto, neto = (num_libre(x) or 0 for x in pares[-1])
    return (bruto, neto) if bruto >= neto > 0 else None


def _suma_bultos_por_contenedor(texto):
    """Suma las cantidades del tipo '= 1785 CARTONS' (una por contenedor en los BL de consolidado)."""
    total = 0
    for l in texto.upper().splitlines():
        if "TOTAL" in l:
            continue
        for m in re.finditer(r"=\s*(\d[\d.,]*)\s*(?:CARTONS?|CTNS?|PACKAGES?|PKGS?|PALLETS?|BULTOS|CAJAS|BOXES|CASES)\b", l):
            total += num_libre(m.group(1)) or 0
    return round(total)


def _rangos(nums):
    out, ini, prev = [], None, None
    for n in nums:
        if ini is None:
            ini = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{ini}" if ini == prev else f"{ini}-{prev}")
            ini = prev = n
    if ini is not None:
        out.append(f"{ini}" if ini == prev else f"{ini}-{prev}")
    return ", ".join(out)


def _fecha(s):
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s or "")
    return (int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


MESES = ["ENE|JAN", "FEB", "MAR", "ABR|APR", "MAY", "JUN", "JUL", "AGO|AUG", "SET|SEP", "OCT", "NOV", "DIC|DEC"]


def _fecha_en_texto(fecha, texto):
    a, m, d = fecha
    t = texto.upper()
    patrones = [
        rf"\b0?{d}\s*[./-]\s*0?{m}\s*[./-]\s*(?:{a}|{a % 100:02d})\b",
        rf"\b{a}\s*[./-]\s*0?{m}\s*[./-]\s*0?{d}\b",
        rf"\b0?{m}\s*[./-]\s*0?{d}\s*[./-]\s*(?:{a}|{a % 100:02d})\b",
        rf"\b0?{d}\s*[-/.]?\s*(?:DE\s+)?(?:{MESES[m - 1]})[A-Z]*\.?\s*(?:DE\s+|,\s*|[-/]\s*)?(?:{a}|{a % 100:02d})\b",
        rf"(?:{MESES[m - 1]})[A-Z]*\.?\s*0?{d}(?:ST|ND|RD|TH)?\s*,?\s*{a}\b",
    ]
    return any(re.search(p, t) for p in patrones)


def _pais(origen):
    """'156 CHINA, REPUBLIC' -> 'CHINA'"""
    nombre = re.sub(r"^\d+\s+", "", origen).split(",")[0].strip()
    for pais, claves in PAISES.items():
        if any(re.fullmatch(k, nombre) for k in claves):
            return pais
    return nombre


PAISES = {
    "CHINA": ["CHINA", "P\\.?R\\.?C\\.?"],
    "CHILE": ["CHILE"],
    "COLOMBIA": ["COLOMBIA"],
    "ARGENTINA": ["ARGENTINA"],
    "BRASIL": ["BRASIL", "BRAZIL"],
    "INDIA": ["INDIA"],
    "ALEMANIA": ["ALEMANIA", "GERMANY", "DEUTSCHLAND"],
    "ITALIA": ["ITALIA", "ITALY", "ITALI"],
    "ESPAÑA": ["ESPA[ÑN]A", "SPAIN"],
    "ESTADOS UNIDOS": ["ESTADOS UNIDOS", "U\\.?S\\.?A\\.?", "UNITED STATES", "E\\.?E\\.?U\\.?U\\.?"],
    "HONG KONG": ["HONG KONG"],
    "POLONIA": ["POLONIA", "POLAND"],
    "MEXICO": ["M[EÉ]XICO"],
    "PERU": ["PER[UÚ]"],
    "PARAGUAY": ["PARAGUAY"],
    "VIETNAM": ["VIET ?NAM"],
    "TAIWAN": ["TAIWAN"],
    "TURQUIA": ["TURQU[IÍ]A", "TURKEY"],
    "BANGLADESH": ["BANGLADESH"],
    "PAKISTAN": ["PAKIST[AÁ]N"],
    "INDONESIA": ["INDONESIA"],
    "TAILANDIA": ["TAILANDIA", "THAILAND"],
    "JAPON": ["JAP[OÓ]N", "JAPAN"],
    "COREA": ["COREA", "KOREA"],
    "FRANCIA": ["FRANCIA", "FRANCE"],
    "PORTUGAL": ["PORTUGAL"],
    "CANADA": ["CANAD[AÁ]"],
    "MALASIA": ["MALASIA", "MALAYSIA"],
}
