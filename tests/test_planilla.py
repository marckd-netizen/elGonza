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
