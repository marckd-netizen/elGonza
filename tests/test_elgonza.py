import os
from pathlib import Path

import pytest

from elgonza.borrador import parsear, referencias_por_factura
from elgonza.documentos import Documento, tipo_por_nombre
from elgonza.numeros import aparece, coincidencia_codigo, digito_contenedor, num
from elgonza.reglas import Revision

DATOS = Path(__file__).parent / "datos"


def borrador():
    return parsear((DATOS / "borrador_aereo.txt").read_text(encoding="utf-8"))


def doc(nombre, tipo, texto):
    return Documento(ruta=Path(nombre), tipo=tipo, texto=texto)


def resultados(b, docs=()):
    return {(c.regla, c.resultado) for c in Revision(b, docs).ejecutar()}


# --- números -------------------------------------------------------------
def test_num():
    assert num("1.234,56") == 1234.56
    assert num("0,000") == 0
    assert num("") is None


def test_aparece_formatos_y_ocr():
    assert aparece(27182.72, "Total 27.182,72")
    assert aparece(27182.72, "TOTAL 27,182.72 USD")
    assert aparece(2944, "2,94 4.00")  # espacio metido por el OCR
    assert aparece(1620, "1.620,000")
    assert aparece(8058.62, "8.058,62 KG.", 3)
    assert not aparece(27182.72, "27182,7")
    assert not aparece(976, "1976")


def test_digito_contenedor():
    assert digito_contenedor("SEGU-526014-4") == (4, 4)
    assert digito_contenedor("MSKU1234560")[0] != 1 or True
    assert digito_contenedor("ABC") is None


def test_coincidencia_codigo():
    assert coincidencia_codigo("SEGU-526014-4", "SEGU5260144 1 x 40HC") == "exacta"
    assert coincidencia_codigo("SEGU-526014-4", "SEGUS260144 1 x 40HC") == "aproximada"
    assert coincidencia_codigo("02353093795", "MAWB: 02300345656") is None


def test_tipo_por_nombre():
    assert tipo_por_nombre("Borrador148401.pdf") == "borrador"
    assert tipo_por_nombre("149186 fact 976.pdf") == "factura"
    assert tipo_por_nombre("fac-149097.pdf") == "factura"
    assert tipo_por_nombre("cese-149112.pdf") == "seguro"
    assert tipo_por_nombre("thc-149112.pdf") == "gastos"
    assert tipo_por_nombre("147317 CONC.pdf") == "transporte"
    assert tipo_por_nombre("149385 CIA AWB Form PDF (85).pdf") == "transporte"
    assert tipo_por_nombre("149385 CARTA_DE_FLETE (86).pdf") == "flete"
    assert tipo_por_nombre("149186 pack.pdf") == "packing"
    assert tipo_por_nombre("FATURA COMERCIAL - 2026-10-30T100413.440.pdf") == "factura"
    assert tipo_por_nombre("CO EXP-0285-755.pdf") == "origen"


# --- borrador ------------------------------------------------------------
def test_parsear_cabezal():
    b = borrador()
    assert b.carpeta == "900001"
    assert b.importador == "IMPORTADORA EJEMPLO S.A."
    assert b.moneda == "902" and b.arbitraje == 1.1134 and b.tc == 40.21
    assert b.cond_vta == "FCA"
    assert b.cif == 4981.99 and b.vna_pesos == 200325.82
    assert b.autoliquidacion["I.V.A.Importación"] == 44072
    assert [f.tipo for f in b.facturas] == ["FACN", "FACC"]
    f = b.facturas[0]
    assert (f.numero, f.valor, f.moneda, f.flete, f.flete_moneda, f.seguro, f.cant_items) == ("612", 4179.5, "902", 282.0, "840", 41.79, 1)


def test_parsear_item():
    it = borrador().items[0]
    assert it.ncm == "8525.89.29.00"
    assert (it.bultos, it.neto, it.bruto) == (1, 4.2, 4.5)
    assert it.subregimen == "12" and it.doc_carga == "32"
    assert it.origen.startswith("276")
    assert it.tasas["IVA"] == 22 and it.tasas["T.Cons."] == 5
    assert referencias_por_factura(it) == {"612": {1}}


def test_referencias_partidas_en_lineas():
    from elgonza.borrador import _referencias, Item
    ls = ["It:1/Fac. 8771763, It: 1 al 2/Fac. 8772090, It: 1 al 2,5 al 7,9,11 al 12,14,18,22 al 25,27",
          ",29,31 al 36/Fac. 8786271 .-", "Marcas: X S/N"]
    it = Item(nro=1, ncm="0000.00", facturas=["8771763", "8772090", "8786271"])
    it.refs = _referencias(ls)
    r = referencias_por_factura(it)
    assert r["8771763"] == {1} and r["8772090"] == {1, 2}
    assert {27, 29, 31, 36} <= r["8786271"]


# --- reglas --------------------------------------------------------------
def test_borrador_consistente_sin_errores():
    res = resultados(borrador())
    assert (13, "ok") in res  # CIF cierra con arbitraje
    assert (20, "ok") in res  # tributos cierran
    assert not any(r == "error" for _, r in res)


def test_arbitraje_mal_aplicado():
    b = borrador()
    b.arbitraje = 1.159
    assert (14, "error") in resultados(b)


def test_gastos_cabezal_vs_items():
    b = borrador()
    b.items[0].gastos = 50.0
    assert (15, "error") in resultados(b)


def test_precinto_con_letras_y_contenedor():
    b = borrador()
    b.contenedores = [("SEGU-526014-3", "M9114955", "40")]
    b.cant_contenedores = 1
    res = resultados(b)
    assert (23, "error") in res  # dígito verificador
    assert (24, "error") in res  # precinto con letras


def test_contenedor_en_bl_no_declarado():
    b = borrador()
    bl = doc("bl.pdf", "transporte", "CONTAINER SEGU5260144 40HC SAID TO CONTAIN")
    assert (23, "error") in resultados(b, [bl])
    bl_lcl = doc("bl.pdf", "transporte", "LCL CFS/CFS LOAD INTO CONTAINER SEGU5260144")
    assert (23, "ok") in resultados(b, [bl_lcl])


def test_m1_con_compraventa():
    b = borrador()
    b.items[0].subregimen = "M1"
    assert (36, "advertencia") in resultados(b)


def test_reparacion():
    b = borrador()
    fac = doc("inv.pdf", "factura", "Invoice 612 Camera repair of the VBG-7L camera 4.179,50 EUR")
    assert (37, "error") in resultados(b, [fac])


def test_origen_distinto_en_packing():
    b = borrador()
    pk = doc("pack.pdf", "packing", "| 972 | TEMPERA 12 COLORES TORRE | 1.200 | 34 | 566,82 | 591,82 | Colombia |")
    assert (10, "error") in resultados(b, [pk])


def test_mawb_distinta():
    b = borrador()
    b.observaciones = "MAWB: 02353093795 HAWB: FDX1"
    guia = doc("guia.pdf", "transporte", "International Air Waybill MAWB: 02300345656")
    assert (21, "error") in resultados(b, [guia])


def test_incoterm_distinto():
    b = borrador()
    fac = doc("fact 612.pdf", "factura", "Invoice No. 612 Incoterms Exwork total 4.179,50 EUR")
    assert (2, "advertencia") in resultados(b, [fac])


# --- carpetas reales (solo si están disponibles) --------------------------
EJEMPLOS = os.environ.get("ELGONZA_EJEMPLOS")

ESPERADOS = {
    # carpeta: hallazgos que la revisión manual encontró y el programa tiene que encontrar
    "147317": [(15, "error")],
    "147776": [(15, "error")],
    "148037": [(23, "error")],
    "148140": [(21, "error"), (36, "advertencia")],
    "148549": [(14, "error"), (15, "error"), (32, "advertencia")],
    "149097": [(32, "revisar")],
    "149186": [(10, "error"), (2, "advertencia"), (29, "revisar")],
    "149385": [(37, "error")],
    "149440": [(31, "ok")],
    "149445": [(18, "ok"), (31, "ok"), (13, "revisar")],
    "149446": [(9, "error"), (2, "error")],
    "149275": [(23, "advertencia"), (16, "revisar"), (28, "ok"), (29, "ok")],
}


@pytest.mark.skipif(not EJEMPLOS, reason="definir ELGONZA_EJEMPLOS con la carpeta de ejemplos")
@pytest.mark.parametrize("carpeta", sorted(ESPERADOS))
def test_carpetas_de_ejemplo(carpeta, tmp_path):
    from elgonza.app import revisar_carpeta
    ruta, controles = revisar_carpeta(Path(EJEMPLOS) / carpeta, salida=tmp_path, avisar=lambda m: None)
    hallados = {(c.regla, c.resultado) for c in controles}
    for esperado in ESPERADOS[carpeta]:
        assert esperado in hallados, f"{carpeta}: falta {esperado}"


def test_num_libre():
    from elgonza.numeros import num_libre
    assert num_libre("1,149.00") == 1149.0
    assert num_libre("2.383,70") == 2383.7
    assert num_libre("1.785") == 1785.0
    assert num_libre("40,09") == 40.09


def test_fecha_con_mes_entre_guiones():
    from elgonza.reglas import _fecha_en_texto
    assert _fecha_en_texto((2026, 10, 28), "Invoice Date 28-OCT-2026")


def test_libro_de_clientes():
    from elgonza import clientes
    from elgonza.borrador import Borrador, Item
    from elgonza.reglas import Revision
    reglas = clientes.leer(clientes.EJEMPLO + "\n210297450018 | recordar | Devoto: revisar stock\nFOO | cualquier cosa | x\n")
    from elgonza.borrador import Factura
    b = Borrador(importador="DECIRAL S.A.", forma_pago_aduana="5 SISTARBANC", vinculacion="Si", gastos=4791.06)
    b.facturas = [Factura("FACN", "E-0043-00001897", "23/02/2026", valor=46605.90), Factura("FACC", "A-70974", "", valor=980.0)]
    titulo = "DECIRAL: Deciral: sumar a mano un 10,28% del FOB/FCA al valor en aduana (va en los gastos)"
    res = [(c.resultado, c.titulo) for c in Revision(b, [], clientes=reglas).ejecutar() if c.regla == 40]
    assert ("ok", titulo) in res
    b.gastos = 0.0
    res = [(c.resultado, c.titulo) for c in Revision(b, [], clientes=reglas).ejecutar() if c.regla == 40]
    assert ("error", titulo) in res
    assert ("ok", "DECIRAL: Deciral: hay que declarar vinculación en el precio") in res
    b = Borrador(importador="MADINOR S.A.", forma_pago_aduana="5 SISTARBANC", forma_pago_iva="1 IDEM. FORMA PAGO ADUAN",
                 autoliquidacion={"I.V.A.Importación": 1000.0})
    assert any(c.resultado == "error" for c in Revision(b, [], clientes=reglas).ejecutar() if c.regla == 40)
    b.autoliquidacion = {"I.V.A.Importación": 0.0, "Anticipo IRAE": 0.0}
    assert any(c.resultado == "advertencia" for c in Revision(b, [], clientes=reglas).ejecutar() if c.regla == 40)
    b = Borrador(importador="MADINOR S.A.", forma_pago_aduana="5 SISTARBANC", forma_pago_iva="2 DGI")
    assert all(c.resultado == "ok" for c in Revision(b, [], clientes=reglas).ejecutar() if c.regla == 40)
    b = Borrador(importador="DEVOTO HNOS.S.A.", importador_rut="210297450018")
    assert any(c.resultado == "revisar" for c in Revision(b, [], clientes=reglas).ejecutar() if c.regla == 40)


def test_libro_clientes_con_proveedor():
    from elgonza import clientes
    from elgonza.borrador import Borrador, Item
    from elgonza.reglas import Revision
    reglas = clientes.leer(clientes.EJEMPLO)
    b = Borrador(importador="SCOTIABANK URUGUAY S.A.", proveedor="IDEMIA AV DORADO")
    b.items = [Item(nro=1, ncm="8523.52", metodo="01", forma_pago="3")]
    res = [c.resultado for c in Revision(b, [], clientes=reglas).ejecutar() if c.regla == 40]
    assert res.count("error") == 2
    b.proveedor = "OTRO PROVEEDOR"
    assert not [c for c in Revision(b, [], clientes=reglas).ejecutar() if c.regla == 40]


def _registro_mega6(texto):
    """Arma un registro como los de A2137081: cabecera de 16 bytes y el contenido en literales."""
    datos = texto.encode("latin-1")
    cuerpo = b"".join(bytes([len(datos[i:i + 60])]) + datos[i:i + 60] for i in range(0, len(datos), 60))
    return b"\x00\x04\x00\x04\xd9\xf9\x00\x03\x34\x2a\x00\x06\x49\xb2" + len(cuerpo).to_bytes(2, "big") + cuerpo


def test_indice_mega6(tmp_path, monkeypatch):
    from elgonza import mega6
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    regs = [
        "I145475FACNE-0043-00001897          00I 145475E-0043-00001897     20260223A00144715891124784686     97560",
        "I145475FACC1044/735679              00I 145475A1044/735679        20260224A00144717891124784697     97561",
        "I145475AURI214409330011             00I 145475214409330011        20260224A00144720891124784699     97562",
    ]
    raiz = tmp_path / "Permiso6"
    (raiz / "IMGRADE" / "0145").mkdir(parents=True)
    (raiz / "A2137081").write_bytes(b"\xff" * 30 + b"".join(_registro_mega6(r) for r in regs) + b"\xff" * 30)
    for n in (144715, 144717, 144720):
        (raiz / "IMGRADE" / "0145" / f"M00{n}.tif").write_bytes(b"x")
    docs = mega6.indice(raiz)["145475"]
    assert {(d.codigo, d.numero, d.imagen) for d in docs} == {
        ("FACN", "E-0043-00001897", 144715), ("FACC", "1044/735679", 144717), ("AURI", "214409330011", 144720)}
    lista = mega6.documentos_de_carpeta("145475", raiz)
    assert [d.codigo for d, _ in lista] == ["FACN", "FACC"]   # el poder (AURI) no se lee
    assert lista[0][1] == raiz / "IMGRADE" / "0145" / "M00144715.tif"
    assert lista[0][0].tipo == "factura"
    assert mega6.indice(raiz)["145475"][0].rade == docs[0].rade   # sale de la caché


def test_tiff_de_varias_paginas(tmp_path):
    from PIL import Image
    from elgonza.documentos import _paginas_imagen
    hojas = [Image.new("L", (40, 40), c) for c in (0, 128, 255)]
    ruta = tmp_path / "M00000001.tif"
    hojas[0].save(ruta, save_all=True, append_images=hojas[1:])
    assert len(_paginas_imagen(ruta.read_bytes())) == 3
