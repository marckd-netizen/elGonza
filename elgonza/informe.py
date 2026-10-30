"""Informe HTML de la revisión de una carpeta."""
import datetime
import html
import json
import zlib

from .numeros import fmt

ETIQUETAS = {
    "error": ("Errores", "Probable error con impacto en tributos o en la validez del DUA"),
    "advertencia": ("Advertencias", "Datos que no coinciden con los documentos"),
    "revisar": ("A revisar", "Puede estar bien; conviene que lo mire una persona"),
    "ok": ("Controles sin observaciones", ""),
}

CSS = """
:root{--fondo:#f7f7f5;--texto:#1f1f1f;--suave:#666;--borde:#ddd;--tarjeta:#fff;
--error:#b42318;--error-f:#fdecea;--adv:#b54708;--adv-f:#fef4e6;--rev:#175cd3;--rev-f:#eaf2fd;--ok:#067647;--ok-f:#e8f6ee}
@media (prefers-color-scheme: dark){:root{--fondo:#161616;--texto:#eee;--suave:#aaa;--borde:#333;--tarjeta:#202020;
--error:#f97066;--error-f:#3a1d1b;--adv:#fdb022;--adv-f:#3a2c14;--rev:#84adff;--rev-f:#1a2740;--ok:#47cd89;--ok-f:#16301f}}
*{box-sizing:border-box}body{margin:0;background:var(--fondo);color:var(--texto);font:15px/1.5 system-ui,Segoe UI,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}
.sub{color:var(--suave);margin:0 0 16px}.datos{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px;margin:12px 0}
.dato{background:var(--tarjeta);border:1px solid var(--borde);border-radius:8px;padding:8px 10px}.dato b{display:block;font-size:12px;color:var(--suave);font-weight:500}
.resumen{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 0}.pill{border-radius:99px;padding:3px 10px;font-weight:600;font-size:13px}
.p-error{background:var(--error-f);color:var(--error)}.p-advertencia{background:var(--adv-f);color:var(--adv)}.p-revisar{background:var(--rev-f);color:var(--rev)}.p-ok{background:var(--ok-f);color:var(--ok)}
.c{background:var(--tarjeta);border:1px solid var(--borde);border-left:4px solid var(--borde);border-radius:8px;padding:10px 12px;margin:6px 0}
.c-error{border-left-color:var(--error)}.c-advertencia{border-left-color:var(--adv)}.c-revisar{border-left-color:var(--rev)}.c-ok{border-left-color:var(--ok)}
.c .t{font-weight:600}.c .d{color:var(--suave);font-size:14px;margin-top:2px}.c .m{font-size:12px;color:var(--suave)}
.fb{margin-top:6px;display:flex;gap:6px;flex-wrap:wrap}.fb button{font:inherit;font-size:12px;border:1px solid var(--borde);background:transparent;color:var(--texto);border-radius:6px;padding:2px 8px;cursor:pointer}
.fb button.sel{background:var(--texto);color:var(--fondo)}.fb input{font:inherit;font-size:12px;flex:1;min-width:160px;border:1px solid var(--borde);background:transparent;color:var(--texto);border-radius:6px;padding:2px 6px}
table{border-collapse:collapse;width:100%;font-size:14px;background:var(--tarjeta)}td,th{border:1px solid var(--borde);padding:4px 8px;text-align:left}
details summary{cursor:pointer;font-weight:600;margin:20px 0 8px}.acciones{margin:24px 0}.acciones button{font:inherit;padding:6px 12px;border-radius:6px;border:1px solid var(--borde);background:var(--tarjeta);color:var(--texto);cursor:pointer}
"""

JS = """
const clave='elgonza-'+document.body.dataset.carpeta;
let fb={};try{fb=JSON.parse(localStorage.getItem(clave)||'{}')}catch(e){}
function guardar(){try{localStorage.setItem(clave,JSON.stringify(fb))}catch(e){}}
document.querySelectorAll('.c[data-id]').forEach(c=>{const id=c.dataset.id;const f=fb[id]||{};
 c.querySelectorAll('button').forEach(b=>{if(f.v===b.dataset.v)b.classList.add('sel');
  b.onclick=()=>{fb[id]=Object.assign(fb[id]||{},{v:b.dataset.v,titulo:c.dataset.titulo,regla:c.dataset.regla});
   c.querySelectorAll('button').forEach(x=>x.classList.toggle('sel',x===b));guardar()}});
 const i=c.querySelector('input');if(i){i.value=f.nota||'';i.oninput=()=>{fb[id]=Object.assign(fb[id]||{},{nota:i.value,titulo:c.dataset.titulo,regla:c.dataset.regla});guardar()}}});
document.getElementById('exportar').onclick=()=>{const datos={carpeta:document.body.dataset.carpeta,fecha:new Date().toISOString(),comentarios:fb};
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(datos,null,2)],{type:'application/json'}));
 a.download='comentarios-'+document.body.dataset.carpeta+'.json';a.click()};
"""


def _e(s):
    return html.escape(str(s or ""))


def generar(borrador, documentos, controles, carpeta):
    b = borrador
    cuenta = {k: sum(1 for c in controles if c.resultado == k) for k in ETIQUETAS}
    partes = [f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Revisión DUA {_e(b.carpeta)}</title><style>{CSS}</style></head><body data-carpeta="{_e(b.carpeta)}"><main>
<h1>Revisión del borrador DUA {_e(b.carpeta)}</h1>
<p class="sub">{_e(b.importador)} · {_e(carpeta)} · {datetime.datetime.now():%d/%m/%Y %H:%M}</p>
<div class="resumen">""" + "".join(
        f'<span class="pill p-{k}">{ETIQUETAS[k][0]}: {cuenta[k]}</span>' for k in ETIQUETAS) + "</div>",
        '<div class="datos">' + "".join(f'<div class="dato"><b>{_e(k)}</b>{_e(v)}</div>' for k, v in [
            ("Aduana", b.aduana), ("Transporte", f"{b.medio_transporte} · {b.transportista}"),
            ("Facturas", f"{len(b.facturas_comerciales)} por {fmt(b.tot_fac)} ({b.moneda_nombre})"),
            ("Condición", b.cond_vta), ("CIF USD", fmt(b.cif)), ("Gastos/Aj USD", fmt(b.gastos)),
            ("Bultos", fmt(b.total_bultos, 0)), ("Bruto / neto kg", f"{fmt(b.peso_bruto, 3)} / {fmt(b.peso_neto, 3)}"),
            ("Ítems", len(b.items)), ("Contenedores", ", ".join(c[0] for c in b.contenedores) or "0"),
        ]) + "</div>"]

    for k, (titulo, explicacion) in ETIQUETAS.items():
        lista = [c for c in controles if c.resultado == k]
        if not lista:
            continue
        cuerpo = "".join(_control(c, i) for i, c in enumerate(lista))
        if k == "ok":
            partes.append(f"<details><summary>{titulo} ({len(lista)})</summary>{cuerpo}</details>")
        else:
            partes.append(f"<h2>{titulo} ({len(lista)})</h2><p class='sub'>{explicacion}</p>{cuerpo}")

    filas = "".join(
        f"<tr><td>{_e(d.nombre)}</td><td>{_e(d.tipo)}</td><td>{'OCR' if d.ocr else 'texto'}</td>"
        f"<td>{_e(d.error) if d.error else ('sin texto' if not d.texto.strip() else f'{len(d.texto):,} caracteres')}</td></tr>"
        for d in documentos)
    partes.append(f"<details><summary>Documentos leídos ({len(documentos)})</summary><table><tr><th>Archivo</th><th>Tipo</th>"
                  f"<th>Lectura</th><th>Resultado</th></tr>{filas}</table></details>")
    partes.append('<div class="acciones"><button id="exportar">Exportar mis comentarios</button>'
                  '<p class="sub">Marcá cada observación como correcta o falsa alarma y exportá el archivo para mejorar las reglas.</p></div>')
    partes.append(f"<script>{JS}</script></main></body></html>")
    return "\n".join(partes)


def _control(c, i):
    id_ = f"{c.regla}-{zlib.crc32(c.titulo.encode()):08x}"
    botones = ""
    if c.resultado != "ok":
        botones = ('<div class="fb"><button data-v="correcto">Es correcto</button><button data-v="falsa">Falsa alarma</button>'
                   '<input placeholder="Comentario (opcional)"></div>')
    return (f'<div class="c c-{c.resultado}" data-id="{id_}" data-titulo="{_e(c.titulo)}" data-regla="{c.regla}">'
            f'<div class="t">{_e(c.titulo)}</div>' + (f'<div class="d">{_e(c.detalle)}</div>' if c.detalle else "")
            + f'<div class="m">{_e(c.tema)} · regla {c.regla}</div>{botones}</div>')


def a_json(borrador, controles):
    return json.dumps({"carpeta": borrador.carpeta, "controles": [c.__dict__ for c in controles]}, ensure_ascii=False, indent=2)
