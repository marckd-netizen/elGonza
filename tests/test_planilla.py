"""Factura a planilla: lectura de facturas y packing lists, pesos y NCM sugerido."""
import openpyxl
import pymupdf as fitz
import pytest

from elgonza import ncm as modulo_ncm
from elgonza.ncm import arancel, sugerir
from elgonza.planilla import _agrupar, _leer_fecha, leer_carpeta, planilla_de_carpeta


@pytest.fixture(autouse=True)
def sin_antecedentes(tmp_path, monkeypatch):
    monkeypatch.setattr(modulo_ncm, "ruta_antecedentes", lambda: tmp_path / "antecedentes_ncm.txt")


def _pdf(ruta, paginas):
    """paginas: [[(x, y, texto)]]"""
    doc = fitz.open()
    for textos in paginas:
        p = doc.new_page(width=595, height=842)
        for x, y, t in textos:
            p.insert_text((x, y), t, fontsize=8, fontname="helv")
    doc.save(ruta)


def _fila(y, nro, codigo, desc, ancho, cant, precio, importe):
    return [(60, y, nro), (95, y, codigo), (170, y, desc), (330, y, ancho), (380, y, cant), (440, y, precio),
            (500, y, importe)]


def _factura(numero, fecha, filas, extra=()):
    enc = [(250, 60, "COMMERCIAL INVOICE"), (60, 90, "TO: Cliente SA"), (380, 90, f"INVOICE NO.: {numero}"),
           (380, 105, f"DATE: {fecha}"), (330, 140, "WIDTH"), (60, 150, "NO."), (95, 150, "CODE"),
           (170, 150, "DESCRIPTION"), (330, 150, "MM"), (380, 150, "M2"), (440, 150, "PRICE"), (500, 150, "AMOUNT")]
    return enc + [t for f in filas for t in f] + list(extra)


def _carpeta(tmp_path):
    filas = [
        _fila(180, "1", "AL-MH", "Semi gloss paper/acrylic/glassine", "350", "22,400.00", "0.232", "5,196.80"),
        _fila(210, "2", "AL-MH", "Semi gloss paper/acrylic/glassine", "120", "480.00", "0.232", "111.36"),
        # código y descripción partidos en dos renglones
        [(95, 232, "PP-"), (170, 232, "Metallized PP film/top coated/")] +
        _fila(240, "3", "", "", "350", "1,386.00", "0.321", "444.91")[0:1] +
        _fila(240, "3", "", "", "350", "1,386.00", "0.321", "444.91")[3:] +
        [(95, 248, "S50#(TC)"), (170, 248, "acrylic/white glassine")],
        _fila(275, "4", "OPAT-W24", "Gloss lamination film / Acrylic", "245", "2,450.00", "0.060", "147.00"),
    ]
    total = [(170, 310, "TOTAL"), (380, 310, "26,716.00"), (500, 310, "5,900.07")]
    packing = [(250, 60, "PACKING LIST"), (380, 90, "INVOICE NO.: FLS-1"), (60, 150, "NO."), (95, 150, "CODE"),
               (380, 150, "M2"), (430, 150, "PCS"), (470, 150, "N.W."), (520, 150, "G.W."),
               (60, 180, "1"), (95, 180, "AL-MH"), (380, 180, "12,000.00"), (430, 180, "1"), (470, 180, "1000"), (520, 180, "1030"),
               (60, 200, "2"), (95, 200, "AL-MH"), (380, 200, "10,400.00"),
               (430, 210, "1"), (470, 210, "900"), (520, 210, "930"),
               (60, 220, "3"), (95, 220, "AL-MH"), (380, 220, "480.00"),
               (60, 245, "4"), (95, 245, "PP-S50#(TC)"), (380, 245, "1,386.00"), (430, 255, "1"), (470, 255, "250"), (520, 255, "270"),
               (60, 265, "5"), (95, 265, "OPAT-W24"), (380, 265, "2,450.00"),
               (60, 300, "TOTAL"), (380, 300, "26,716.00"), (430, 300, "3"), (470, 300, "2,150"), (520, 300, "2,230")]
    _pdf(tmp_path / "FLS-1 invoice.pdf", [_factura("FLS-1", "2026/8/31", filas, total), packing])
    return tmp_path


def test_lee_factura_y_packing(tmp_path):
    (f,) = leer_carpeta(_carpeta(tmp_path), usar_ocr=False)
    assert (f.numero, f.fecha) == ("FLS-1", "31/08/2026")
    assert [(l.codigo, l.cantidad, l.importe) for l in f.lineas] == [
        ("AL-MH", 22400, 5196.80), ("AL-MH", 480, 111.36), ("PP-S50#(TC)", 1386, 444.91), ("OPAT-W24", 2450, 147.00)]
    assert f.lineas[2].descripcion == "Metallized PP film/top coated/ acrylic/white glassine"
    assert {l.unidad for l in f.lineas} == {"METROS CUADRADOS"}
    assert [l.ancho_mm for l in f.lineas] == [350, 120, 350, 245]
    # bulto 2 (900 kg) tiene 10.400 + 480 m² de AL-MH: todo AL-MH suma 1900 kg, repartido por m²
    assert f.lineas[0].neto + f.lineas[1].neto == pytest.approx(1900)
    assert f.lineas[0].neto == pytest.approx(1900 * 22400 / 22880, abs=0.01)
    # el último bulto (250 kg) se reparte entre PP-S50 y OPAT por m²
    assert f.lineas[2].neto == pytest.approx(250 * 1386 / 3836, abs=0.01)
    assert sum(l.bruto for l in f.lineas) == pytest.approx(2230, abs=0.05)
    assert f.neto_total == 2150 and f.bultos == 3


def test_ncm_sugerido(tmp_path):
    (f,) = leer_carpeta(_carpeta(tmp_path), usar_ocr=False)
    assert [l.ncm for l in f.lineas] == ["4811.41.90.00", "4811.41.10.00", "3919.90.10.90", "3919.90.90.00"]
    assert "Acrylic" in f.lineas[3].motivo and "ficha técnica" in f.lineas[3].motivo


def test_planilla_una_factura(tmp_path):
    ruta, _ = planilla_de_carpeta(_carpeta(tmp_path), usar_ocr=False, avisar=lambda m: None)
    ws = openpyxl.load_workbook(ruta)["Hoja1"]
    assert [c.value for c in ws[1]] == ["ITEM", "CANTIDAD", "VALOR", "NCM", "DESCRIPCION", "NETO", "BRUTO",
                                        "UNIDAD MEDIDA", "CODIGO FACTURA", "DESCRIPCION FACTURA", "NCM SUGERIDO"]
    assert [c.value for c in ws[2]][:3] == [1, 22400, 5196.8]
    assert ws["K2"].value == "4811.41.90.00"


def test_planilla_multifactura(tmp_path):
    _carpeta(tmp_path)
    fila = _fila(180, "1", "PVC-20", "White PVC film/acrylic adhesive", "150", "100.00", "2.00", "200.00")
    _pdf(tmp_path / "INV-77.pdf", [_factura("INV-77", "05/09/2026", [fila], [(170, 220, "TOTAL"), (500, 220, "200.00")])])
    ruta, facturas = planilla_de_carpeta(tmp_path, usar_ocr=False, avisar=lambda m: None)
    ws = openpyxl.load_workbook(ruta)["Hoja1"]
    assert [c.value for c in ws[1]][:5] == ["ITEM", "FACTURA", "FECHA", "CANTIDAD", "VALOR"]
    filas = [[c.value for c in r] for r in ws.iter_rows(min_row=2)]
    assert [r[:3] for r in filas] == [[1, "FLS-1", "31/08/2026"], [2, "FLS-1", "31/08/2026"], [3, "FLS-1", "31/08/2026"],
                                      [4, "FLS-1", "31/08/2026"], [5, "INV-77", "05/09/2026"]]
    assert filas[4][-1] == "3919.10.20.00"
    assert any("packing" in o for o in facturas[1].observaciones)


def test_observaciones_de_totales(tmp_path):
    fila = _fila(180, "1", "AB-1", "Thing", "10", "100.00", "2.00", "200.00")
    _pdf(tmp_path / "inv.pdf", [_factura("X-100", "2026-01-02", [fila], [
        (60, 220, "Deduct 3% commission of other invoice"), (500, 220, "6.00"),
        (170, 240, "TOTAL"), (380, 240, "100.00"), (500, 240, "194.00")])])
    (f,) = leer_carpeta(tmp_path, usar_ocr=False)
    obs = " ".join(f.observaciones)
    assert "descuento" in obs and "total igual" in obs and "packing" in obs
    assert f.lineas[0].ncm == ""


def test_fechas():
    assert _leer_fecha("2026/8/31") == "31/08/2026"
    assert _leer_fecha("05/09/26") == "05/09/2026"
    assert _leer_fecha("Aug. 31, 2026") == "31/08/2026"
    assert _leer_fecha("31-AUG-2026") == "31/08/2026"
    assert _leer_fecha("sin fecha") == ""


def test_agrupar_bultos():
    # renglones en y=0,10,20,30; pesos en 0 (solo) y 25 (centro de 20 y 30)
    assert _agrupar([0, 10, 20, 30], [0, 25], 12) == [[0, 1], [2, 3]] or _agrupar([0, 10, 20, 30], [0, 25], 12) == [[0], [1, 2, 3]]
    assert _agrupar([0, 24, 48, 72], [0, 48], 24) == [[0], [1, 2, 3]]


def test_arancel_y_antecedentes(tmp_path):
    t = arancel()
    assert t["3919.10.10.00"].ez == "14.4" and t["4811.41.90.00"].aec == "10.8"
    (tmp_path / "antecedentes_ncm.txt").write_text("OPAT-W24 | 3919.90.10.90 | FILM BOPP PARA LAMINAR\n", encoding="utf-8")
    ncm, desc, motivo = sugerir("OPAT-W24", "Gloss lamination film / Acrylic", 245)
    assert (ncm, desc) == ("3919.90.10.90", "FILM BOPP PARA LAMINAR") and "Antecedente" in motivo
    assert sugerir("X", "Ballpoint pens", None)[0] == ""


# --- factura sin numerar, con la cantidad en un renglón aparte (estilo Madesa) ---
def _bloque(y, cant, letras, desc, cont, precio, total):
    return [(70, y, cant), (110, y, letras), (95, y + 10, "UN"), (120, y + 10, desc), (430, y + 10, precio),
            (500, y + 10, total), (130, y + 20, cont)]


def _madesa(tmp_path):
    enc = [(300, 60, "FACTURA PROFORMA Nº 20260909"), (60, 120, "CANTIDAD"), (95, 130, "UN"),
           (130, 130, "DESCRIPCION DE MERCANCIAS"), (430, 130, "PRECIO UNIT"), (500, 130, "PRECIO TOTAL")]
    filas = (_bloque(150, "60", "(Sesenta)", "COMEDOR LEXY / NEGRO - 340479 /", "341642 - 53295Z1 - NCM: 94036000",
                     "20.24", "1,214.40")
             + _bloque(190, "48", "(Cuarenta y Ocho)", "ROPERO TOKIO BLANCO 3 PUERTAS - 341061 - 109509 -",
                       "NCM: 94035000", "125.96", "6,046.08"))
    pie = [(60, 240, "MONTO TOTAL MERCANCIAS"), (500, 240, "7,260.48"), (60, 260, "BOM PRINCIPIO, 27 DE OCTUBRE DE 2026.")]
    _pdf(tmp_path / "Fatura 20260909.pdf", [enc + filas + pie])
    pack = [(300, 60, "PACKING LIST Nº 20260909"), (380, 120, "PESOS (Kg)"), (380, 130, "NETO"), (430, 130, "BRUTO"),
            (480, 130, "CBM"),
            (70, 150, "60"), (110, 150, "(Sesenta)"), (95, 160, "UN"), (120, 160, "COMEDOR LEXY / NEGRO - 340479"),
            (380, 160, "906"), (430, 160, "918"), (480, 160, "2.284"), (130, 170, "/ 341642 - 53295Z1"),
            (70, 190, "48"), (110, 190, "(Cuarenta y Ocho)"), (95, 200, "UN"), (120, 200, "ROPERO TOKIO BLANCO - 341061 -"),
            (380, 200, "4,972.8"), (430, 200, "5,126.4"), (480, 200, "11.581"), (130, 210, "109509 - NCM: 94035000"),
            (60, 240, "TOTAL DE BULTOS: 204"), (60, 252, "PESO NETO: 5,878.8 Kg  PESO BRUTO: 6,044.4 Kg")]
    _pdf(tmp_path / "Packing list 20260909.pdf", [pack])
    return tmp_path


def test_factura_sin_numerar_cantidad_arriba(tmp_path):
    (f,) = leer_carpeta(_madesa(tmp_path), usar_ocr=False)
    assert (f.numero, f.fecha) == ("20260909", "27/10/2026")
    assert [(l.nro, l.codigo, l.cantidad, l.precio, l.importe, l.unidad) for l in f.lineas] == [
        ("1", "340479", 60, 20.24, 1214.40, "UNIDADES"), ("2", "341061", 48, 125.96, 6046.08, "UNIDADES")]
    assert f.lineas[0].descripcion == "COMEDOR LEXY / NEGRO - 340479 / 341642 - 53295Z1 - NCM: 94036000"
    assert [(l.neto, l.bruto) for l in f.lineas] == [(906, 918), (4972.8, 5126.4)]
    assert (f.neto_total, f.bruto_total, f.bultos) == (5878.8, 6044.4, 204)
    # el NCM de la factura, llevado a su apertura nacional
    assert [l.ncm for l in f.lineas] == ["9403.60.00.00", "9403.50.00.00"]
    assert "NCM de la factura" in f.lineas[0].motivo
    assert any("proforma" in o for o in f.observaciones)


def test_ncm_valvulas():
    casos = {
        "BRASS UNIVERSAL CHECK VALVE 3/4\"": "8481.30.00.90",
        "BRASS DOUBLE GUIDE FOOT VALVE 3\"": "8481.30.00.90",
        "BRASS SAFETY VALVE 1/2\"": "8481.40.00.90",
        "BRASS BALL VALVE 1030 S FF PRESS": "8481.80.95.00",
        "GAS VALVE WITH ALUMINIUM YELLOW BUTTERFLY HANDLE": "8481.80.95.00",
        "SS304BALL Valve with IRON HANDLE": "8481.80.95.00",
        "BRASS BALL BIBCOCKS 1/2 x 3/4": "8481.80.19.00",
    }
    for desc, esperado in casos.items():
        assert sugerir("", desc)[0] == esperado, desc
    assert sugerir("", "BRASS SAFETY VALVE")[1] == "VALVULA DE SEGURIDAD DE LATON"


def test_titulo_de_grupo_no_entra_en_la_descripcion(tmp_path):
    enc = [(250, 60, "COMMERCIAL INVOICE"), (380, 90, "INVOICE NO.: SAS-1"), (60, 140, "Art"), (160, 140, "Description"),
           (400, 140, "PCS"), (450, 140, "USD/PC"), (510, 140, "USD")]
    filas = [(160, 160, "CHECK VALVE"),
             (60, 169, "VV01"), (160, 169, "BRASS CHECK VALVE"), (400, 169, "144"), (440, 169, "$"), (450, 169, "2.38"),
             (500, 169, "$"), (510, 169, "342.72"),
             (60, 178, "VV02"), (160, 178, "BRASS CHECK VALVE"), (400, 178, "216"), (440, 178, "$"), (450, 178, "3.09"),
             (500, 178, "$"), (510, 178, "667.44"),
             (160, 188, "BRASS VALVE"),
             (160, 198, "BRASS BALL VALVE 1030 S FF"),
             (60, 206, "V03"), (160, 206, "COATED FLAT HANDLE"), (400, 206, "10"), (440, 206, "$"), (450, 206, "2.01"),
             (500, 206, "$"), (510, 206, "20.10"),
             (160, 214, "SAS / ON-OFF"),
             (60, 240, "TOTAL:"), (400, 240, "370"), (510, 240, "1,030.26")]
    _pdf(tmp_path / "inv.pdf", [enc + filas])
    (f,) = leer_carpeta(tmp_path, usar_ocr=False)
    assert [l.descripcion for l in f.lineas] == [
        "BRASS CHECK VALVE", "BRASS CHECK VALVE", "BRASS BALL VALVE 1030 S FF COATED FLAT HANDLE SAS / ON-OFF"]
