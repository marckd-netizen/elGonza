/*
 * elGonza · VUCE AANI
 *
 * Carga en VUCE (portal.vuce.gub.uy), con la sesión ya abierta del usuario, un
 * certificado AANI (DGSA · Certificado provisorio para importación de alimento
 * para animales) por cada fila de una tabla, y arma una planilla con
 * "operación | denominación comercial | registro".
 *
 * El flujo que automatiza (igual que a mano):
 *   INICIAR TRAMITE → Inicio Operación: Tipo de documento, Nro Documento,
 *   Código de régimen, Entidad, Trámite → CONFIRMAR → Operación NNNN:
 *   "…" en la línea AANI → Ejecutar → formulario (Número de Registro, Monto CIF,
 *   Kilogramos Bruto y Neto, Punto de ingreso / Transporte, y los valores fijos)
 *   → ENVIAR.
 *
 * Todo el estado queda en chrome.storage.local, así si la página se recarga
 * en el medio el proceso sigue desde donde estaba.
 */
(() => {
  'use strict';
  if (window.top !== window || window.__vaCargado) return;
  window.__vaCargado = true;

  const CLAVE = 'va_estado';
  const CLAVE_LOCK = 'va_lock';
  const URL_INICIO = 'https://portal.vuce.gub.uy/VUCE/servlet/com.vuce.landingdespachanteperformance';
  const YO = Math.random().toString(36).slice(2);

  const FIJOS_POR_DEFECTO = `[Inicio]
Tipo de documento = RUT
Nro Documento = 216456390013
Código de régimen = Registro/Especiales
Entidad = MGAP
Trámite = AANI

[Formulario]
Datos del Beneficiario / Teléfono = 095764038
Datos del Beneficiario / Email = isidorilaura@gmail.com
Datos / La partida ingresará en forma = Total
Datos / País de Origen = IRLANDA (EIRE)
Datos / Provincia o Estado de Origen = NEWBRIDGE
Datos / País de Procedencia = IRLANDA (EIRE)
Datos / Provincia o Estado de Procedencia = NEWBRIDGE
Identificación de la Partida / NCM = 2309909090
Destino de la Mercadería / Depósito = Agropecuaria Rio Negro
Destino de la Mercadería / Dirección = República de Corea 3499
Destino de la Mercadería / Departamento = Montevideo
Destino de la Mercadería / Seccional = 13
Destino de la Mercadería / Email = isidorilaura@gmail.com
Destino de la Mercadería / Teléfono de Contacto = 094453550
Datos Despachante / Nombre = MICHELINI Y GAMBAROTTA SRL
Datos Despachante / Dirección = MISIONES 1486 PISO 7
Datos Despachante / Teléfono = 29155646
Datos Despachante / Email = import@michelini.com.uy
Datos Despachante / RUT = 217901210010
Datos Despachante / Nº de Despachante = 0723`;

  // Las dos combinaciones de ingreso. Se prueban los textos en orden contra las opciones de la lista.
  const INGRESOS = {
    maritimo: { punto: ['Puerto de Montevideo', 'Montevideo'], transporte: ['Marítimo', 'Maritimo'], nombre: 'Puerto de Montevideo / Marítimo' },
    aereo: { punto: ['Carrasco', 'Aeropuerto de Carrasco'], transporte: ['Aéreo', 'Aereo'], nombre: 'Carrasco / Aéreo' },
  };

  // ---------------------------------------------------------------- estado

  let estado = {
    filas: [],          // {registro, cif, bruto, neto, ingreso, extra:{campo:valor}, estado, operacion, producto, detalle}
    corriendo: false,
    actual: -1,
    paso: '',
    intentos: 0,
    pausarAntes: true,
    pausa: null,
    urlInicio: '',
    fijos: FIJOS_POR_DEFECTO,
    log: [],
  };
  let detener = false;
  let ejecutando = false;
  let respuestaPausa = null;

  const guardar = () => chrome.storage.local.set({ [CLAVE]: estado });
  const cargar = async () => {
    const r = await chrome.storage.local.get(CLAVE);
    if (r[CLAVE]) estado = Object.assign(estado, r[CLAVE]);
  };

  function log(msg) {
    const h = new Date().toLocaleTimeString('es-UY', { hour12: false });
    estado.log.push(`${h}  ${msg}`);
    if (estado.log.length > 300) estado.log = estado.log.slice(-300);
    guardar();
    render();
  }

  // Un solo "trabajador" a la vez, aunque haya varias pestañas de VUCE abiertas.
  let latido = null;
  async function tomarLock(esperarMs) {
    const fin = Date.now() + esperarMs;
    for (;;) {
      const r = (await chrome.storage.local.get(CLAVE_LOCK))[CLAVE_LOCK];
      if (!r || r.id === YO || Date.now() - r.t > 6000) {
        await chrome.storage.local.set({ [CLAVE_LOCK]: { id: YO, t: Date.now() } });
        await sleep(300);
        const r2 = (await chrome.storage.local.get(CLAVE_LOCK))[CLAVE_LOCK];
        if (r2 && r2.id === YO) {
          clearInterval(latido);
          latido = setInterval(() => chrome.storage.local.set({ [CLAVE_LOCK]: { id: YO, t: Date.now() } }), 2000);
          return true;
        }
      }
      if (Date.now() > fin) return false;
      await sleep(1000);
    }
  }
  async function soltarLock() {
    clearInterval(latido); latido = null;
    const r = (await chrome.storage.local.get(CLAVE_LOCK))[CLAVE_LOCK];
    if (r && r.id === YO) await chrome.storage.local.remove(CLAVE_LOCK);
  }
  window.addEventListener('pagehide', () => { if (latido) { clearInterval(latido); chrome.storage.local.remove(CLAVE_LOCK); } });

  // ---------------------------------------------------------------- DOM

  const norm = (s) => String(s == null ? '' : s).normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[º°]/g, 'o').replace(/\s+/g, ' ').trim().toLowerCase();
  /** Texto de una etiqueta sin el "*" de obligatorio ni los dos puntos. */
  const etiq = (s) => norm(s).replace(/[\s*:]+$/g, '').replace(/\s*\*\s*/g, ' ').trim();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function documentos() {
    const res = [];
    const visitar = (doc) => {
      if (!doc || res.includes(doc)) return;
      res.push(doc);
      for (const f of doc.querySelectorAll('iframe, frame')) {
        try { visitar(f.contentDocument); } catch (e) { /* otro origen */ }
      }
    };
    visitar(document);
    return res;
  }

  function visible(el) {
    if (!el || !el.isConnected) return false;
    if (panelHost && panelHost.contains(el)) return false;
    if (!el.getClientRects().length) return false;
    const win = el.ownerDocument.defaultView;
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const cs = win.getComputedStyle(n);
      if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') return false;
    }
    const fe = win.frameElement;
    if (fe && !visible(fe)) return false;
    return true;
  }

  function textoDe(el) {
    if (el.tagName === 'INPUT') return el.value || '';
    return el.innerText || el.textContent || '';
  }

  const SEL_TEXTO = 'a, button, input[type=button], input[type=submit], span, div, td, th, label, li, b, strong, font, p, h1, h2, h3, h4, legend, img[alt]';
  const SEL_CONTROL = 'input:not([type=hidden]):not([type=button]):not([type=submit]):not([type=image]), select, textarea';

  /** Elementos visibles cuyo texto es exactamente `texto` (sin tildes ni mayúsculas). Los más internos. */
  function buscarTexto(texto, raiz) {
    const t = norm(texto);
    const hallados = [];
    for (const doc of raiz ? [raiz] : documentos()) {
      for (const el of doc.querySelectorAll(SEL_TEXTO)) {
        let v = el.tagName === 'IMG' ? el.alt : textoDe(el);
        if (!v.trim() && el.title) v = el.title;
        if (v.length > texto.length + 40) continue;
        if (norm(v) === t && visible(el)) hallados.push(el);
      }
    }
    return hallados.filter((el) => !hallados.some((o) => o !== el && el.contains(o)));
  }

  function textoPagina() {
    return documentos().map((d) => (d.body ? d.body.innerText : '')).join('\n');
  }

  function clic(el) {
    try { el.scrollIntoView({ block: 'center' }); } catch (e) { /* nada */ }
    const win = el.ownerDocument.defaultView;
    const op = { bubbles: true, cancelable: true, view: win };
    el.dispatchEvent(new win.MouseEvent('mousedown', op));
    el.dispatchEvent(new win.MouseEvent('mouseup', op));
    if (typeof el.click === 'function') el.click();
    else el.dispatchEvent(new win.MouseEvent('click', op));
  }

  async function esperar(fn, ms, que) {
    const fin = Date.now() + ms;
    for (;;) {
      if (detener) throw new Error('DETENIDO');
      let v = null;
      try { v = fn(); } catch (e) { v = null; }
      if (v) return v;
      if (Date.now() > fin) throw new Error(`No apareció: ${que}`);
      await sleep(300);
    }
  }

  /** El recuadro de una sección del formulario ("Datos del Beneficiario", "Datos Despachante"…). */
  function seccion(nombre) {
    const n = norm(nombre);
    for (const doc of documentos()) {
      const cands = [...doc.querySelectorAll('legend, h1, h2, h3, h4, h5, span, div, td, a, label')]
        .filter((el) => norm(textoDe(el)).replace(/^[▾▼▸►\s]+/, '') === n && visible(el));
      for (const el of cands.filter((c) => !cands.some((o) => o !== c && c.contains(o)))) {
        const fs = el.closest('fieldset');
        if (fs) return fs;
        for (let p = el.parentElement, i = 0; p && i < 6; p = p.parentElement, i++) {
          if (p.querySelector(SEL_CONTROL)) return p;
        }
      }
    }
    return null;
  }

  /** Etiquetas visibles (lo más internas posible) cuyo texto es `etiqueta`, dentro de `raiz` o de toda la página. */
  function etiquetas(etiqueta, raiz, prefijo) {
    const e = etiq(etiqueta);
    const raices = raiz ? [raiz] : documentos();
    const res = [];
    for (const r of raices) {
      const cands = [...r.querySelectorAll('label, span, td, th, div, p, b, strong, font')].filter((el) => {
        const t = etiq(el.innerText || el.textContent);
        if (t.length > e.length + 60) return false;
        return (prefijo ? t.startsWith(e) : t === e) && visible(el);
      });
      res.push(...cands.filter((c) => !cands.some((o) => o !== c && c.contains(o))));
    }
    return res;
  }

  /** El control (input / select / textarea) de la etiqueta `etiqueta` ("Sección / Campo" también vale). */
  function control(clave) {
    const { sec, campo } = partirClave(clave);
    const raiz = sec ? seccion(sec) : null;
    if (sec && !raiz) return null;
    for (const lab of etiquetas(campo, raiz)) {
      const doc = lab.ownerDocument;
      const f = lab.getAttribute && lab.getAttribute('for');
      if (f) {
        const c = doc.getElementById(f);
        if (c && c.matches(SEL_CONTROL) && visible(c)) return c;
      }
      const interno = lab.querySelector && lab.querySelector(SEL_CONTROL);
      if (interno && visible(interno)) return interno;
      // el primer control visible que viene después de la etiqueta (a la derecha o abajo)
      const ctrls = [...(raiz || doc).querySelectorAll(SEL_CONTROL)];
      const sig = ctrls.find((c) => (lab.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING) && visible(c));
      if (sig) return sig;
    }
    return null;
  }

  /** Texto de solo lectura que acompaña a una etiqueta (p. ej. "Nombre: EQ-INTERNACIONAL LTDA."). */
  function textoJunto(clave) {
    const { sec, campo } = partirClave(clave);
    const raiz = sec ? seccion(sec) : null;
    for (const lab of etiquetas(campo, raiz, true)) {
      const doc = lab.ownerDocument;
      const tw = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
      tw.currentNode = lab;
      let n = tw.nextNode();
      while (n && lab.contains(n)) n = tw.nextNode();
      for (let i = 0; n && i < 40; n = tw.nextNode(), i++) {
        if (n.nodeType === 1) {
          if (n.matches(SEL_CONTROL)) return n.value ? n.value.trim() : '';
          continue;
        }
        const t = n.textContent.replace(/\s+/g, ' ').trim();
        if (!t || !visible(n.parentElement)) continue;
        if (/[:*]\s*$/.test(t)) return ''; // ya es la etiqueta siguiente
        // texto dentro de otra etiqueta ("Monto CIFF (U$S)" + "*" + ":" en piezas separadas)
        for (let p = n.parentElement, k = 0; p && k < 3; p = p.parentElement, k++) {
          if (p.contains(lab)) break;
          const pt = (p.innerText || p.textContent || '').replace(/\s+/g, ' ').trim();
          if (p.tagName === 'LABEL' || (pt.length < 120 && /:\s*$/.test(pt))) return '';
        }
        return t;
      }
    }
    return '';
  }

  function partirClave(clave) {
    const i = clave.indexOf(' / ');
    if (i < 0) return { sec: '', campo: clave.trim() };
    return { sec: clave.slice(0, i).trim(), campo: clave.slice(i + 3).trim() };
  }

  /** La opción de un select que corresponde a `quiero` (o a alguno de los textos de la lista). */
  function opcion(sel, quiero) {
    const lista = Array.isArray(quiero) ? quiero : [quiero];
    const ops = [...sel.options].filter((o) => norm(o.text) && !/^\(?seleccion/.test(norm(o.text)));
    for (const q of lista) {
      const n = norm(q);
      const exacta = ops.find((o) => norm(o.text) === n || norm(o.value) === n);
      if (exacta) return exacta;
      const empieza = ops.filter((o) => norm(o.text).startsWith(n));
      if (empieza.length) return empieza.sort((a, b) => a.text.length - b.text.length)[0];
      const contiene = ops.filter((o) => norm(o.text).includes(n));
      if (contiene.length) return contiene.sort((a, b) => a.text.length - b.text.length)[0];
    }
    return null;
  }

  function disparar(el, ...tipos) {
    const win = el.ownerDocument.defaultView;
    for (const t of tipos) {
      const ev = t === 'focus' || t === 'blur' ? new win.FocusEvent(t, { bubbles: false })
        : t === 'focusout' || t === 'focusin' ? new win.FocusEvent(t, { bubbles: true })
          : new win.Event(t, { bubbles: true });
      el.dispatchEvent(ev);
    }
  }

  function valorActual(el) {
    if (el.tagName === 'SELECT') return el.selectedIndex >= 0 ? el.options[el.selectedIndex].text : '';
    if (el.type === 'checkbox') return el.checked ? 'si' : 'no';
    return el.value;
  }

  function yaTiene(el, valor) {
    if (el.tagName === 'SELECT') {
      const op = opcion(el, valor);
      return !!op && el.value === op.value;
    }
    if (el.type === 'checkbox') return el.checked === /^(s[ií]|x|true|1)$/i.test(String(valor).trim());
    return norm(el.value) === norm(valor);
  }

  /** Pone `valor` en el control como si lo escribiera el usuario. Devuelve false si el select no tiene esa opción. */
  function poner(el, valor) {
    const win = el.ownerDocument.defaultView;
    if (el.tagName === 'SELECT') {
      const op = opcion(el, valor);
      if (!op) return false;
      if (el.value === op.value) return true;
      disparar(el, 'focus', 'focusin');
      el.value = op.value;
      disparar(el, 'input', 'change', 'blur', 'focusout');
      return true;
    }
    if (el.type === 'checkbox') {
      const quiero = /^(s[ií]|x|true|1)$/i.test(String(valor).trim());
      if (el.checked !== quiero) clic(el);
      return true;
    }
    const proto = el.tagName === 'TEXTAREA' ? win.HTMLTextAreaElement.prototype : win.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    el.focus();
    disparar(el, 'focus', 'focusin');
    setter.call(el, String(valor));
    disparar(el, 'input', 'keyup', 'change', 'blur', 'focusout');
    try { el.blur(); } catch (e) { /* nada */ }
    return true;
  }

  /** Espera el campo, pone el valor y comprueba que quedó. */
  async function completar(clave, valor, ms = 20000) {
    const el = await esperar(() => {
      const c = control(clave);
      if (!c) return null;
      if (c.tagName === 'SELECT' && !opcion(c, valor)) return null; // la lista todavía no se cargó
      return c;
    }, ms, `el campo "${clave}" con la opción "${[].concat(valor)[0]}"`);
    if (!yaTiene(el, valor)) poner(el, valor);
    await sleep(400);
    return el;
  }

  /** El recuadro rojo "Hay N errores en el formulario", si está. */
  function cajaErrores() {
    for (const doc of documentos()) {
      const cands = [...doc.querySelectorAll('span, div, p, h1, h2, h3, h4, td, b, strong')].filter((el) => {
        const t = norm(el.innerText || el.textContent);
        return t.length < 80 && /^hay \d+ error/.test(t) && visible(el);
      });
      const el = cands.filter((c) => !cands.some((o) => o !== c && c.contains(o)))[0];
      if (!el) continue;
      let caja = el;
      for (let p = el.parentElement, i = 0; p && i < 4; p = p.parentElement, i++) {
        if ((p.innerText || '').length > 1500) break;
        caja = p;
        if (p.querySelector('ol, ul')) break;
      }
      return caja;
    }
    return null;
  }

  // ---------------------------------------------------------------- tabla de entrada

  const RE_NUM = /^-?[\d.,\s]+$/;
  /** "1.234,5" → "1234.5"; "900" → "900"; "15,3" → "15.3" (VUCE usa el punto como separador de decimales). */
  function numero(v) {
    if (typeof v === 'number') return String(Math.round(v * 1000) / 1000);
    let s = String(v || '').replace(/\s|US\$|U\$S|\$|kg/gi, '').trim();
    if (!s) return '';
    const c = s.lastIndexOf(','); const p = s.lastIndexOf('.');
    if (c >= 0 && p >= 0) {
      s = c > p ? s.replace(/\./g, '').replace(',', '.') : s.replace(/,/g, '');
    } else if (c >= 0) {
      s = s.replace(',', '.');
    } else if ((s.match(/\./g) || []).length > 1) {
      s = s.replace(/\./g, '');
    }
    return s;
  }

  function tipoIngreso(v) {
    const n = norm(v);
    if (!n) return '';
    if (/aere|carrasco|avion|^a$/.test(n)) return 'aereo';
    if (/marit|puerto|montevideo|mvd|barco|buque|^m$/.test(n)) return 'maritimo';
    return '';
  }

  const COLUMNAS = [
    ['registro', /registro/],
    ['cif', /cif|monto/],
    ['bruto', /bruto/],
    ['neto', /neto/],
    ['ingreso', /ingreso|transporte|via|puerto|aereo|maritimo/],
    ['producto', /producto|denominacion|nombre/],
  ];

  /** Convierte las filas (arrays) en filas de trabajo. La primera puede ser de títulos. */
  function filasDeTabla(aoa) {
    const filas = aoa.map((f) => f.map((c) => (c == null ? '' : c))).filter((f) => f.some((c) => String(c).trim()));
    if (!filas.length) return [];
    const cab = filas[0].map((c) => norm(c));
    const tieneTitulos = cab.some((c) => COLUMNAS.some(([, re]) => re.test(c)) && !RE_NUM.test(c));
    let mapa;
    if (tieneTitulos) {
      mapa = cab.map((c) => {
        const k = COLUMNAS.find(([, re]) => re.test(c));
        return k ? k[0] : (c ? 'extra:' + String(filas[0][cab.indexOf(c)]).trim() : null);
      });
      filas.shift();
    } else {
      mapa = ['registro', 'cif', 'bruto', 'neto', 'ingreso'];
    }
    return filas.map((f) => {
      const r = { registro: '', cif: '', bruto: '', neto: '', ingreso: '', extra: {}, estado: 'pendiente', operacion: '', producto: '', detalle: '' };
      mapa.forEach((k, i) => {
        if (!k || f[i] === undefined) return;
        const v = f[i];
        if (k.startsWith('extra:')) { if (String(v).trim()) r.extra[k.slice(6)] = String(v).trim(); return; }
        if (k === 'producto') return; // lo lee de VUCE
        if (k === 'ingreso') { r.ingreso = tipoIngreso(v); r.ingresoTexto = String(v); return; }
        if (k === 'registro') { r.registro = String(typeof v === 'number' ? Math.round(v) : v).trim(); return; }
        r[k] = numero(v);
      });
      const faltan = [];
      if (!r.registro) faltan.push('Número de Registro');
      if (!r.cif) faltan.push('Monto CIF');
      if (!r.bruto) faltan.push('Kilogramos Bruto');
      if (!r.neto) faltan.push('Kilogramos Neto');
      if (!r.ingreso) faltan.push('ingreso (Montevideo/Marítimo o Carrasco/Aéreo)');
      if (faltan.length) { r.estado = 'error'; r.detalle = 'Falta en la tabla: ' + faltan.join(', '); }
      return r;
    });
  }

  function leerPegado(texto) {
    const aoa = texto.replace(/\r/g, '').split('\n').map((l) => (l.includes('\t') ? l.split('\t') : l.split(/;|,(?=\s*\D)/)));
    return filasDeTabla(aoa);
  }

  async function leerArchivo(file) {
    const buf = await file.arrayBuffer();
    const wb = XLSX.read(buf, { type: 'array' });
    const ws = wb.Sheets[wb.SheetNames[0]];
    return filasDeTabla(XLSX.utils.sheet_to_json(ws, { header: 1, raw: true, defval: '' }));
  }

  function cargarFilas(filas) {
    if (!filas.length) { alert('No encontré filas en la tabla.'); return; }
    estado.filas = filas;
    estado.actual = -1;
    estado.paso = '';
    const malas = filas.filter((f) => f.estado === 'error').length;
    log(`Cargadas ${filas.length} fila(s)${malas ? `, ${malas} con datos incompletos (se saltean)` : ''}.`);
  }

  /** Lee el cuadro de valores fijos: {inicio: [[clave, valor]], formulario: [[clave, valor]]}. */
  function leerFijos() {
    const res = { inicio: [], formulario: [] };
    let parte = 'formulario';
    for (const linea of String(estado.fijos || '').split('\n')) {
      const l = linea.trim();
      if (!l || l.startsWith('#')) continue;
      const m = /^\[(.+)\]$/.exec(l);
      if (m) { parte = norm(m[1]).startsWith('inicio') ? 'inicio' : 'formulario'; continue; }
      const i = l.indexOf('=');
      if (i < 0) continue;
      res[parte].push([l.slice(0, i).trim(), l.slice(i + 1).trim()]);
    }
    return res;
  }

  // ---------------------------------------------------------------- flujo

  async function irAlPaso(p) {
    estado.paso = p;
    await guardar();
    render();
  }

  const enInicioOperacion = () => !!control('Tipo de documento') && /inicio operaci/i.test(norm(textoPagina()));
  const botonIniciar = () => buscarTexto('Iniciar tramite')[0] || buscarTexto('Iniciar trámite')[0];
  const RE_OPERACION = /operaci[oó]n\s*(?:n[°ºo.]*\s*)?(\d{6,})/i;
  const enFormulario = () => !!control('Número de Registro');

  function numeroOperacion() {
    const t = textoJunto('N° de Operación');
    if (/^\d{6,}$/.test(t)) return t;
    const m = RE_OPERACION.exec(textoPagina());
    return m ? m[1] : '';
  }

  async function irAlInicio() {
    if (!estado.urlInicio) estado.urlInicio = URL_INICIO;
    if (estado.intentos > 3) throw new Error('No pude volver a la página de inicio de VUCE');
    estado.intentos++;
    await guardar();
    log('Voy a la página de inicio de VUCE…');
    location.href = estado.urlInicio;
    await sleep(30000);
    throw new Error('La página de inicio no cargó');
  }

  async function pasoInicio(f) {
    if (enInicioOperacion()) return;
    let b = null;
    try { b = await esperar(botonIniciar, 8000, 'el botón "INICIAR TRAMITE"'); } catch (e) {
      if (e.message === 'DETENIDO') throw e;
      await irAlInicio();
    }
    log(`Fila ${estado.actual + 1} (registro ${f.registro}): INICIAR TRAMITE`);
    clic(b);
    await esperar(enInicioOperacion, 30000, 'la pantalla "Inicio Operación"');
  }

  async function pasoOperacion(f) {
    const fijos = leerFijos().inicio;
    const extra = Object.entries(f.extra || {});
    const valor = (k) => {
      const e = extra.find(([c]) => etiq(c) === etiq(k));
      if (e) return e[1];
      const x = fijos.find(([c]) => etiq(c) === etiq(k));
      return x ? x[1] : '';
    };
    const tipoDoc = valor('Tipo de documento') || 'RUT';
    const nroDoc = valor('Nro Documento');
    if (!nroDoc) throw new Error('Falta "Nro Documento" en los valores fijos [Inicio]');
    await completar('Tipo de documento', tipoDoc);
    await sleep(800);
    await completar('Nro Documento', nroDoc);
    const nombre = await esperar(() => textoJunto('Nombre'), 20000, `el nombre del RUT ${nroDoc}`);
    log(`Beneficiario: ${nombre}`);
    for (const [k, v] of fijos) {
      if (['tipo de documento', 'nro documento'].includes(etiq(k))) continue;
      await completar(k, valor(k), 25000);
      await sleep(800);
    }
    // repaso: que no se haya borrado nada al recargarse las listas
    for (const [k] of fijos) {
      const c = control(k);
      if (c && !yaTiene(c, valor(k))) { poner(c, valor(k)); await sleep(1000); }
    }
  }

  async function pasoConfirmarOperacion(f) {
    const b = await esperar(() => buscarTexto('Confirmar')[0], 15000, 'el botón "CONFIRMAR"');
    await irAlPaso('confirmando');
    clic(b);
    log(`Fila ${estado.actual + 1}: CONFIRMAR (se crea la operación)`);
    await esperarOperacion(f);
  }

  async function esperarOperacion(f) {
    const op = await esperar(() => {
      if (enFormulario()) return numeroOperacion() || 'form';
      const t = textoPagina();
      const m = RE_OPERACION.exec(t);
      return m && /tr[aá]mites pendientes|agregar un tr[aá]mite/i.test(t) ? m[1] : null;
    }, 60000, 'la pantalla de la operación');
    if (op !== 'form') {
      f.operacion = op;
      log(`Fila ${estado.actual + 1}: operación ${op}`);
    }
    await irAlPaso('operacion_creada');
  }

  /** El "…" de la línea AANI en "Trámites pendientes". */
  function menuAANI() {
    const tramite = etiq((leerFijos().inicio.find(([k]) => etiq(k) === 'tramite') || [])[1] || 'AANI').split(/\s|-/)[0];
    const cands = [];
    for (const doc of documentos()) {
      for (const el of doc.querySelectorAll('span, div, td, b, strong, p, a, label')) {
        if (norm(textoDe(el)) === tramite && visible(el)) cands.push(el);
      }
    }
    for (const el of cands.filter((c) => !cands.some((o) => o !== c && c.contains(o)))) {
      const fila = el.closest('tr') || el.parentElement && el.parentElement.parentElement;
      if (!fila) continue;
      const ctl = [...fila.querySelectorAll('a, button, img, i, span, div, [role=button]')].filter((e) => visible(e) && !e.contains(el));
      const puntos = ctl.find((e) => /^(\.{3}|…|•••|⋯|···)$/.test((textoDe(e) || '').trim()))
        || ctl.find((e) => /accion|opcion|menu|more|mas/i.test(`${e.title || ''} ${e.getAttribute('aria-label') || ''} ${e.className || ''}`));
      if (puntos) return puntos;
      const ult = ctl.filter((e) => e.matches('a, button, img, [role=button]'));
      if (ult.length) return ult[ult.length - 1];
    }
    return null;
  }

  async function pasoEjecutar(f) {
    if (enFormulario()) return;
    if (!f.operacion) { const op = numeroOperacion(); if (op) f.operacion = op; }
    const m = await esperar(menuAANI, 30000, 'el "…" de la línea AANI');
    clic(m);
    const ej = await esperar(() => buscarTexto('Ejecutar')[0], 10000, 'la opción "Ejecutar"');
    const link = ej.closest('a');
    if (link) link.removeAttribute('target'); // que abra en esta misma pestaña
    await irAlPaso('formulario');
    log(`Fila ${estado.actual + 1}: Ejecutar AANI`);
    clic(link || ej);
    await esperar(enFormulario, 60000, 'el formulario "Solicitud de Importación de Alimentos para Animales"');
  }

  function valoresFormulario(f) {
    const ing = INGRESOS[f.ingreso];
    const lista = [
      ['Número de Registro', f.registro],
      ['Monto CIFF (U$S)', f.cif],
      ['Kilogramos Bruto', f.bruto],
      ['Kilogramos Neto', f.neto],
    ];
    const fijos = leerFijos().formulario.filter(([k]) => !/punto de ingreso|identificacion del transporte/.test(etiq(k)));
    lista.push(...fijos);
    lista.push(['Punto de ingreso al País', ing.punto], ['Identificación del Transporte', ing.transporte]);
    // columnas extra de la tabla pisan lo anterior
    for (const [k, v] of Object.entries(f.extra || {})) {
      const i = lista.findIndex(([c]) => etiq(c) === etiq(k) || etiq(partirClave(c).campo) === etiq(k));
      if (i >= 0) lista[i] = [lista[i][0], v]; else lista.push([k, v]);
    }
    return lista;
  }

  /** "Monto CIFF (U$S)" puede llamarse distinto: probar variantes. */
  function controlFlexible(clave) {
    const c = control(clave);
    if (c) return { c, clave };
    if (/^monto cif/.test(etiq(clave))) {
      for (const k of ['Monto CIF (U$S)', 'Monto CIFF', 'Monto CIF']) {
        const x = control(k); if (x) return { c: x, clave: k };
      }
      for (const lab of etiquetas('Monto CIF', null, true)) {
        const ctrls = [...lab.ownerDocument.querySelectorAll(SEL_CONTROL)];
        const sig = ctrls.find((x) => (lab.compareDocumentPosition(x) & Node.DOCUMENT_POSITION_FOLLOWING) && visible(x));
        if (sig) return { c: sig, clave };
      }
    }
    return null;
  }

  async function pasoFormulario(f) {
    await esperar(enFormulario, 60000, 'el formulario AANI');
    if (!f.operacion) f.operacion = numeroOperacion();
    await sleep(1000);
    // 1) el registro primero: VUCE busca el producto
    const reg = await esperar(() => control('Número de Registro'), 20000, 'el campo "Número de Registro"');
    if (!yaTiene(reg, f.registro)) poner(reg, f.registro);
    let producto = '';
    try {
      producto = await esperar(() => {
        const t = textoJunto('Denominación comercial del alimento');
        return t && !/^\(|^recuerde/i.test(t) ? t : null;
      }, 25000, 'la denominación comercial');
    } catch (e) {
      if (e.message === 'DETENIDO') throw e;
    }
    f.producto = producto;
    log(`Fila ${estado.actual + 1}: registro ${f.registro} → ${producto || '(VUCE no mostró la denominación)'}`);
    await guardar();

    const lista = valoresFormulario(f);
    const noEncontrados = [];
    for (let vuelta = 0; vuelta < 3; vuelta++) {
      noEncontrados.length = 0;
      let cambios = 0;
      for (const [k, v] of lista) {
        if (detener) throw new Error('DETENIDO');
        if (v === '' || v == null) continue;
        const r = controlFlexible(k);
        if (!r) { noEncontrados.push(k); continue; }
        if (r.c.tagName === 'SELECT' && !opcion(r.c, v)) {
          // puede que la lista se esté cargando
          try { await esperar(() => opcion(r.c, v), 8000, k); } catch (e) {
            if (e.message === 'DETENIDO') throw e;
            noEncontrados.push(`${k} (no hay la opción "${[].concat(v)[0]}")`); continue;
          }
        }
        if (!yaTiene(r.c, v)) {
          poner(r.c, v);
          cambios++;
          await sleep(r.c.tagName === 'SELECT' ? 1200 : 250);
        }
      }
      if (!cambios) break;
      await sleep(1500);
    }
    return { producto, noEncontrados };
  }

  function preguntar(texto) {
    estado.pausa = texto;
    guardar();
    render();
    return new Promise((res) => { respuestaPausa = res; });
  }

  async function pasoEnviar(f) {
    const b = await esperar(() => buscarTexto('Enviar')[0], 15000, 'el botón "ENVIAR"');
    const vieja = cajaErrores();
    if (vieja) vieja.setAttribute('data-elgonza-vista', norm(vieja.innerText));
    await irAlPaso('enviado');
    clic(b.closest('a, button') || b);
    log(`Fila ${estado.actual + 1}: ENVIAR`);
    return esperarResultado(f);
  }

  /** Después de ENVIAR: 'ok' si el formulario se fue, o el texto de los errores si VUCE lo rechazó. */
  async function esperarResultado(f, recargada) {
    const fin = Date.now() + 120000;
    let sinCambio = 0;
    for (;;) {
      if (detener) throw new Error('DETENIDO');
      await sleep(700);
      const caja = cajaErrores();
      const nueva = caja && (recargada || caja.getAttribute('data-elgonza-vista') !== norm(caja.innerText));
      if (nueva && enFormulario()) return { errores: caja.innerText.replace(/\n+/g, ' · ').replace(/\s+/g, ' ').trim() };
      if (!enFormulario()) {
        await sleep(1500);
        if (!enFormulario()) return { ok: true };
      }
      if (recargada && enFormulario() && ++sinCambio > 20) return { errores: 'VUCE volvió a mostrar el formulario sin decir por qué' };
      if (Date.now() > fin) throw new Error('VUCE no respondió al ENVIAR');
    }
  }

  async function procesar(i) {
    const f = estado.filas[i];
    if (estado.paso === 'inicio') {
      await pasoInicio(f);
      await irAlPaso('datos_operacion');
    }
    if (estado.paso === 'datos_operacion') {
      if (!enInicioOperacion()) { await irAlPaso('inicio'); return procesar(i); }
      await pasoOperacion(f);
      await pasoConfirmarOperacion(f);
    }
    if (estado.paso === 'confirmando') await esperarOperacion(f);
    if (estado.paso === 'operacion_creada') await pasoEjecutar(f);
    if (estado.paso === 'formulario') {
      const r = await pasoFormulario(f);
      await irAlPaso('pausa');
      const avisos = [];
      if (!r.producto) avisos.push('VUCE no mostró la denominación comercial: revisá el número de registro.');
      if (r.noEncontrados.length) avisos.push('No encontré: ' + r.noEncontrados.join(', ') + '.');
      if (avisos.length || estado.pausarAntes) {
        const resp = await preguntar(`Operación ${f.operacion || '?'} · ${r.producto || f.registro}: `
          + (avisos.length ? avisos.join(' ') + ' Completalo a mano si hace falta y ' : 'revisá el formulario y ')
          + 'apretá Continuar para ENVIAR.');
        if (resp === 'detener') throw new Error('DETENIDO');
        if (resp !== 'continuar') throw new Error('SALTEADO');
      }
    }
    if (estado.paso === 'pausa') {
      estado.pausa = null;
      for (;;) {
        const r = await pasoEnviar(f);
        if (r.ok) break;
        await irAlPaso('pausa');
        log(`Fila ${i + 1}: VUCE rechazó el envío: ${r.errores}`);
        const resp = await preguntar(`VUCE no aceptó el formulario: ${r.errores}. Corregilo en la pantalla y apretá Continuar para volver a ENVIAR.`);
        if (resp === 'detener') throw new Error('DETENIDO');
        if (resp !== 'continuar') throw new Error('SALTEADO');
        estado.pausa = null;
      }
      await terminarFila(f);
    }
    if (estado.paso === 'enviado') {
      const r = await esperarResultado(f, true);
      if (r.errores) {
        await irAlPaso('pausa');
        const resp = await preguntar(`VUCE no aceptó el formulario: ${r.errores}. Corregilo y apretá Continuar para volver a ENVIAR.`);
        if (resp === 'detener') throw new Error('DETENIDO');
        if (resp !== 'continuar') throw new Error('SALTEADO');
        estado.pausa = null;
        return procesar(i);
      }
      await terminarFila(f);
    }
  }

  async function terminarFila(f) {
    f.estado = 'ok';
    f.detalle = '';
    log(`Fila ${estado.actual + 1}: enviada · operación ${f.operacion} · ${f.producto}`);
    await irAlPaso('hecho');
  }

  async function correr() {
    if (ejecutando) return;
    ejecutando = true;
    detener = false;
    try {
      if (!(await tomarLock(15000))) { log('Hay otra pestaña de VUCE trabajando; sigo ahí.'); return; }
      while (estado.corriendo) {
        let i = estado.actual;
        if (i < 0 || !estado.filas[i] || estado.filas[i].estado !== 'pendiente') {
          i = estado.filas.findIndex((x) => x.estado === 'pendiente');
          if (i < 0) break;
          estado.actual = i;
          estado.paso = 'inicio';
          estado.intentos = 0;
          await guardar();
        }
        const f = estado.filas[i];
        try {
          await procesar(i);
        } catch (e) {
          if (e.message === 'DETENIDO') break;
          const conOp = f.operacion ? `La operación ${f.operacion} quedó creada: completala o anulala en VUCE. ` : '';
          if (e.message === 'SALTEADO') {
            f.estado = 'error'; f.detalle = conOp + 'Salteada por el usuario (no se envió)';
          } else if (estado.paso === 'enviado') {
            f.estado = 'revisar'; f.detalle = 'Se apretó ENVIAR pero no pude confirmar el resultado: ' + e.message;
          } else {
            f.estado = 'error'; f.detalle = conOp + 'No se envió. ' + e.message;
          }
          log(`Fila ${i + 1}: ${f.detalle}`);
        }
        estado.pausa = null;
        estado.actual = -1;
        await guardar();
        render();
        if (!estado.corriendo || !estado.filas.some((x) => x.estado === 'pendiente')) break;
        // a la página de inicio para la siguiente fila
        await sleep(1000);
        if (!botonIniciar()) {
          estado.intentos = 0;
          await guardar();
          location.href = estado.urlInicio || URL_INICIO;
          await sleep(30000);
        }
      }
    } finally {
      ejecutando = false;
      if (!detener && estado.corriendo && !estado.filas.some((x) => x.estado === 'pendiente')) {
        const ok = estado.filas.filter((x) => x.estado === 'ok').length;
        log(`Terminado: ${ok} de ${estado.filas.length} enviada(s).`);
        estado.corriendo = false;
      }
      if (detener) estado.corriendo = false;
      estado.pausa = null;
      await guardar();
      await soltarLock();
      render();
    }
  }

  /** Al cargar la página: si había un proceso en marcha, seguirlo. */
  async function reanudar() {
    if (!estado.corriendo) return;
    const f = estado.filas[estado.actual];
    if (f && f.estado === 'pendiente' && estado.paso === 'datos_operacion' && !enInicioOperacion()) estado.paso = 'inicio';
    if (f && f.estado === 'pendiente' && estado.paso === 'pausa') estado.paso = enFormulario() ? 'formulario' : 'operacion_creada';
    estado.pausa = null;
    await guardar();
    log('Sigo con el proceso…');
    await sleep(1500);
    correr();
  }

  // ---------------------------------------------------------------- resultados

  function filasResultado() {
    const cab = ['Operación', 'Denominación comercial', 'Número de Registro', 'Monto CIF', 'Kg bruto', 'Kg neto', 'Ingreso', 'Observación'];
    return [cab].concat(estado.filas.map((f) => [
      f.operacion || '', f.producto || '', f.registro, f.cif, f.bruto, f.neto,
      f.ingreso ? INGRESOS[f.ingreso].nombre : (f.ingresoTexto || ''),
      f.estado === 'ok' ? '' : f.estado === 'pendiente' ? 'Pendiente' : f.detalle,
    ]));
  }

  function descargarPlanilla() {
    if (!estado.filas.length) return;
    const aoa = filasResultado().map((r, i) => (i ? r.map((c, j) => (j >= 3 && j <= 5 && c !== '' && !isNaN(+c) ? +c : c)) : r));
    const ws = XLSX.utils.aoa_to_sheet(aoa);
    ws['!cols'] = [{ wch: 12 }, { wch: 34 }, { wch: 12 }, { wch: 11 }, { wch: 9 }, { wch: 9 }, { wch: 28 }, { wch: 60 }];
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'AANI');
    const d = new Date();
    const p = (n) => String(n).padStart(2, '0');
    const nombre = `vuce_aani_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}.xlsx`;
    const out = XLSX.write(wb, { bookType: 'xlsx', type: 'array' });
    bajar(new Blob([out], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }), nombre);
  }

  function bajar(blob, nombre) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = nombre;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  async function copiar() {
    const txt = filasResultado().map((f) => f.join('\t')).join('\n');
    try { await navigator.clipboard.writeText(txt); log('Copiado: pegalo en Excel.'); } catch (e) { alert(txt); }
  }

  function diagnostico() {
    const partes = documentos().map((doc, i) => {
      let url = '';
      try { url = doc.location.href; } catch (e) { /* nada */ }
      return `<!-- ===== documento ${i}: ${url} ===== -->\n${doc.documentElement.outerHTML}`;
    });
    partes.unshift(`<!-- log:\n${estado.log.join('\n').replace(/--/g, '- -')}\n-->`);
    bajar(new Blob([partes.join('\n\n')], { type: 'text/html' }), 'diagnostico_vuce.html');
  }

  // ---------------------------------------------------------------- panel

  let panelHost = null;
  let raiz = null;
  let abierto = false;
  let verFijos = false;

  const CSS = `
    :host { all: initial; }
    * { box-sizing: border-box; font-family: Segoe UI, Arial, sans-serif; }
    .fab { position: fixed; right: 18px; bottom: 18px; z-index: 2147483647; background: #1b4f8a; color: #fff;
      border: 0; border-radius: 22px; padding: 10px 16px; font-size: 14px; font-weight: 600; cursor: pointer;
      box-shadow: 0 3px 10px rgba(0,0,0,.3); }
    .panel { position: fixed; right: 18px; bottom: 70px; z-index: 2147483647; width: 560px; max-height: 82vh;
      display: flex; flex-direction: column; background: #fff; color: #222; border: 1px solid #b8c4d4; border-radius: 8px;
      box-shadow: 0 6px 24px rgba(0,0,0,.25); font-size: 13px; }
    .cab { background: #1b4f8a; color: #fff; padding: 8px 12px; font-weight: 600; display: flex; justify-content: space-between;
      border-radius: 8px 8px 0 0; }
    .cab button { background: none; border: 0; color: #fff; font-size: 16px; cursor: pointer; }
    .cuerpo { padding: 10px 12px; overflow: auto; }
    .fila { display: flex; gap: 6px; flex-wrap: wrap; margin: 6px 0; align-items: center; }
    button.b { border: 1px solid #1b4f8a; background: #fff; color: #1b4f8a; border-radius: 4px; padding: 5px 10px;
      font-size: 13px; cursor: pointer; }
    button.b.p { background: #1b4f8a; color: #fff; }
    button.b:disabled { opacity: .45; cursor: default; }
    textarea { width: 100%; font-family: Consolas, monospace; font-size: 12px; border: 1px solid #b8c4d4; border-radius: 4px; padding: 5px; }
    table { width: 100%; border-collapse: collapse; margin: 6px 0; }
    th, td { text-align: left; padding: 3px 4px; border-bottom: 1px solid #e3e8ef; vertical-align: top; }
    th { font-size: 12px; color: #555; }
    .ok { color: #17803d; font-weight: 600; } .error { color: #b42318; } .revisar { color: #b54708; font-weight: 600; }
    .act { background: #eef4fb; }
    .det { font-size: 11px; color: #666; }
    .pausa { background: #fff6e0; border: 1px solid #f0c36d; border-radius: 6px; padding: 8px; margin: 6px 0; }
    .log { font-family: Consolas, monospace; font-size: 11px; background: #f6f8fa; padding: 6px; height: 100px;
      overflow: auto; white-space: pre-wrap; border-radius: 4px; }
    .nota { font-size: 11px; color: #666; margin: 4px 0; }
    label.chk { display: flex; gap: 6px; align-items: center; margin: 6px 0; }
    input[type=file] { display: none; }
  `;

  function crearPanel() {
    panelHost = document.createElement('div');
    panelHost.id = 'elgonza-vuce-aani';
    raiz = panelHost.attachShadow({ mode: 'open' });
    raiz.innerHTML = `<style>${CSS}</style><button class="fab" id="fab">AANI · VUCE</button><div id="pan"></div>`;
    document.documentElement.appendChild(panelHost);
    raiz.getElementById('fab').addEventListener('click', () => { abierto = !abierto; render(); });
  }

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function render() {
    if (!raiz) return;
    const pan = raiz.getElementById('pan');
    if (estado.corriendo || estado.pausa) abierto = true;
    if (!abierto) { pan.innerHTML = ''; return; }
    // no pisar lo que el usuario está escribiendo
    const act = raiz.activeElement;
    if (act && act.tagName === 'TEXTAREA' && !estado.corriendo) return;
    const ok = estado.filas.filter((f) => f.estado === 'ok').length;
    const pend = estado.filas.filter((f) => f.estado === 'pendiente').length;
    const filas = estado.filas.map((f, i) => `
      <tr class="${i === estado.actual ? 'act' : ''}">
        <td>${i + 1}</td>
        <td>${esc(f.registro)}</td>
        <td>${esc(f.cif)} · ${esc(f.bruto)}/${esc(f.neto)} kg · ${f.ingreso === 'aereo' ? 'Aéreo' : f.ingreso === 'maritimo' ? 'Marítimo' : '?'}</td>
        <td class="${f.estado}">${f.estado === 'ok' ? esc(f.operacion) : f.estado === 'pendiente'
          ? (i === estado.actual ? (estado.pausa ? 'Esperando' : 'Trabajando…') : '—') : f.estado === 'revisar' ? 'Revisar' : 'Error'}
          ${f.producto ? `<div class="det">${esc(f.producto)}</div>` : ''}${f.detalle ? `<div class="det">${esc(f.detalle)}</div>` : ''}</td>
      </tr>`).join('');
    pan.innerHTML = `
      <div class="panel">
        <div class="cab"><span>AANI · VUCE</span><button id="cerrarPanel" title="Ocultar">×</button></div>
        <div class="cuerpo">
          <div class="nota">Pegá las filas desde Excel (Registro, Monto CIF, Kg bruto, Kg neto, Ingreso: <b>Montevideo</b> o <b>Carrasco</b>) o elegí un archivo.</div>
          <textarea id="tPegar" rows="4" placeholder="Registro&#9;CIF&#9;Bruto&#9;Neto&#9;Ingreso&#10;185359&#9;900&#9;15&#9;10&#9;Carrasco" ${estado.corriendo ? 'disabled' : ''}></textarea>
          <div class="fila">
            <button class="b" id="bPegar" ${estado.corriendo ? 'disabled' : ''}>Cargar lo pegado</button>
            <button class="b" id="bArchivo" ${estado.corriendo ? 'disabled' : ''}>Elegir planilla…</button>
            <input type="file" id="iArchivo" accept=".xlsx,.xls,.xlsm,.csv,.ods">
            <button class="b" id="bFijos">${verFijos ? 'Ocultar' : 'Ver'} valores fijos</button>
          </div>
          ${verFijos ? `<div class="nota">Un campo por línea: <b>Sección / Campo = valor</b>. [Inicio] es la pantalla "Inicio Operación".</div>
            <textarea id="tFijos" rows="14" ${estado.corriendo ? 'disabled' : ''}>${esc(estado.fijos)}</textarea>
            <div class="fila"><button class="b" id="bGuardarFijos">Guardar valores fijos</button>
            <button class="b" id="bFijosDef">Volver a los de fábrica</button></div>` : ''}
          ${estado.filas.length ? `<table><tr><th>#</th><th>Registro</th><th>Datos</th><th>Operación</th></tr>${filas}</table>
            <div class="nota">${ok} enviada(s) · ${pend} pendiente(s) · ${estado.filas.length} en total</div>` : ''}
          ${estado.pausa ? `<div class="pausa">${esc(estado.pausa)}
            <div class="fila"><button class="b p" id="bSeguir">Continuar (ENVIAR)</button>
            <button class="b" id="bSaltear">Saltear esta</button></div></div>` : ''}
          <label class="chk"><input type="checkbox" id="cPausa" ${estado.pausarAntes ? 'checked' : ''}>
            Parar antes de ENVIAR cada una, para revisar el formulario</label>
          <div class="fila">
            <button class="b p" id="bIniciar" ${estado.corriendo || !pend ? 'disabled' : ''}>Iniciar</button>
            <button class="b" id="bDetener" ${estado.corriendo ? '' : 'disabled'}>Detener</button>
            <button class="b" id="bBajar" ${estado.filas.length ? '' : 'disabled'}>Descargar planilla</button>
            <button class="b" id="bCopiar" ${estado.filas.length ? '' : 'disabled'}>Copiar</button>
          </div>
          <div class="nota">Cada fila crea una operación en VUCE. Dejá esta pestaña abierta mientras trabaja.</div>
          <div class="log" id="log">${esc(estado.log.slice(-60).join('\n'))}</div>
          <div class="fila">
            <button class="b" id="bLimpiar" ${estado.corriendo ? 'disabled' : ''}>Vaciar lista</button>
            <button class="b" id="bDiag">Diagnóstico</button>
          </div>
        </div>
      </div>`;
    const $ = (id) => raiz.getElementById(id);
    const lg = $('log'); lg.scrollTop = lg.scrollHeight;
    $('cerrarPanel').onclick = () => { abierto = false; render(); };
    $('bPegar').onclick = () => { const t = $('tPegar').value; $('tPegar').blur(); cargarFilas(leerPegado(t)); };
    $('bArchivo').onclick = () => $('iArchivo').click();
    $('iArchivo').onchange = async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      try { cargarFilas(await leerArchivo(file)); } catch (err) { alert('No pude leer la planilla: ' + err.message); }
    };
    $('bFijos').onclick = () => { verFijos = !verFijos; render(); };
    if ($('bGuardarFijos')) {
      $('bGuardarFijos').onclick = () => { estado.fijos = $('tFijos').value; $('tFijos').blur(); log('Valores fijos guardados.'); };
      $('bFijosDef').onclick = () => {
        if (!confirm('¿Volver a los valores fijos de fábrica?')) return;
        estado.fijos = FIJOS_POR_DEFECTO; $('tFijos').blur(); log('Valores fijos de fábrica.');
      };
    }
    $('cPausa').onchange = (e) => { estado.pausarAntes = e.target.checked; guardar(); };
    $('bIniciar').onclick = () => {
      const n = estado.filas.filter((f) => f.estado === 'pendiente').length;
      if (!confirm(`Se van a crear y enviar ${n} solicitud(es) AANI en VUCE. ¿Seguimos?`)) return;
      estado.corriendo = true;
      estado.actual = -1;
      if (botonIniciar()) estado.urlInicio = location.href;
      log('Inicio.');
      correr();
    };
    $('bDetener').onclick = () => {
      detener = true;
      estado.corriendo = false;
      if (respuestaPausa) { respuestaPausa('detener'); respuestaPausa = null; }
      log('Detenido por el usuario.');
    };
    if ($('bSeguir')) {
      $('bSeguir').onclick = () => { estado.pausa = null; render(); if (respuestaPausa) respuestaPausa('continuar'); respuestaPausa = null; };
      $('bSaltear').onclick = () => { estado.pausa = null; render(); if (respuestaPausa) respuestaPausa('saltear'); respuestaPausa = null; };
    }
    $('bBajar').onclick = descargarPlanilla;
    $('bCopiar').onclick = copiar;
    $('bLimpiar').onclick = async () => {
      if (estado.filas.length && !confirm('¿Vaciar la lista y los resultados?')) return;
      estado.filas = []; estado.log = []; estado.actual = -1; await guardar(); render();
    };
    $('bDiag').onclick = diagnostico;
  }

  // para las pruebas
  window.__vaPruebas = { filasDeTabla, leerPegado, numero, tipoIngreso, opcion, leerFijos: () => leerFijos(), control, textoJunto };

  // ---------------------------------------------------------------- arranque

  (async () => {
    await cargar();
    crearPanel();
    render();
    reanudar();
  })();
})();
