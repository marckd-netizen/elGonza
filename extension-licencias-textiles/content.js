/*
 * elGonza · Licencias textiles DNI
 *
 * Sube, de a una, las planillas Excel de licencia textil al sitio de DNI
 * (tramitesdni.miem.gub.uy, sistema Apia) usando la sesión ya abierta del usuario,
 * lee el número de trámite que devuelve el sitio y arma una planilla con
 * "archivo | VU<número>".
 *
 * El flujo que automatiza (igual que a mano):
 *   Inicio de procesos → Licencia Textiles → Tipo ingreso: Planilla Excel →
 *   Seleccionar archivo → (ventana Documentos) subir el archivo → Confirmar →
 *   Importar → Confirmar → mensaje "El número de trámite asociado es: …" → Cerrar.
 *
 * Todo el estado queda en chrome.storage.local, así si la página se recarga
 * en el medio el proceso sigue desde donde estaba.
 */
(() => {
  'use strict';
  if (window.top !== window || window.__ltCargado) return;
  window.__ltCargado = true;

  const CLAVE = 'lt_estado';
  const PREF_ARCH = 'lt_arch_';
  const PASOS_ANTES_DE_ENVIAR = ['inicio', 'tipo', 'seleccionar', 'subir', 'importar', 'revisar', 'pausa'];
  const RE_TRAMITE = /n[uú]mero de tr[aá]mite asociado es:?\s*(\d{6,})/i;

  // ---------------------------------------------------------------- estado

  let estado = {
    archivos: [],        // {nombre, tamano, estado: pendiente|ok|error|revisar, vu, detalle}
    corriendo: false,
    actual: -1,
    paso: '',
    intentos: 0,
    pausarAntes: true,
    pausa: null,         // texto cuando espera al usuario
    urlInicio: '',
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

  // ---------------------------------------------------------------- DOM

  const norm = (s) => (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/\s+/g, ' ').trim().toLowerCase();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  /** El documento principal y todos los iframes del mismo sitio, recursivo. */
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
    // si está dentro de un iframe oculto
    const fe = win.frameElement;
    if (fe && !visible(fe)) return false;
    return true;
  }

  function textoDe(el) {
    if (el.tagName === 'INPUT') return el.value || '';
    return el.innerText || el.textContent || '';
  }

  const SEL_CLICABLES = 'a, button, input[type=button], input[type=submit], span, div, td, th, label, li, b, strong, font, p, img[alt], legend';

  /** Elementos visibles cuyo texto es exactamente `texto` (sin tildes ni mayúsculas). Se queda con los más internos. */
  function buscarTexto(texto) {
    const t = norm(texto);
    const hallados = [];
    for (const doc of documentos()) {
      for (const el of doc.querySelectorAll(SEL_CLICABLES)) {
        const v = el.tagName === 'IMG' ? el.alt : textoDe(el);
        if (v.length > texto.length + 40) continue;
        if (norm(v) === t && visible(el)) hallados.push(el);
      }
    }
    return hallados.filter((el) => !hallados.some((o) => o !== el && el.contains(o)));
  }

  /** Largo del texto del menor ancestro de `el` que contiene `marca` (Infinity si ninguno). */
  function cercania(el, marca) {
    const m = norm(marca);
    for (let n = el.parentElement, i = 0; n && i < 25; n = n.parentElement, i++) {
      const tx = norm(n.innerText || n.textContent);
      if (tx.includes(m)) return tx.length;
    }
    return Infinity;
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

  /** La ventana "Mensajes" del sitio, si está abierta: {texto, cerrar}. */
  function leerMensaje() {
    for (const tit of buscarTexto('Mensajes')) {
      let caja = tit.parentElement;
      for (let i = 0; caja && i < 8; caja = caja.parentElement, i++) {
        const cerrar = [...caja.querySelectorAll(SEL_CLICABLES)]
          .find((e) => norm(textoDe(e)) === 'cerrar' && visible(e));
        if (cerrar) {
          if ((caja.innerText || '').length > 2500) break;
          const texto = (caja.innerText || '').replace(/^\s*Mensajes\s*/i, '').replace(/\s*Cerrar\s*$/i, '').trim();
          if (texto) return { texto, cerrar };
          break;
        }
      }
    }
    // a veces el mensaje no tiene el título: buscar el número directo
    for (const doc of documentos()) {
      const m = RE_TRAMITE.exec(doc.body ? doc.body.innerText : '');
      if (m) return { texto: m[0], cerrar: null };
    }
    return null;
  }

  function selectTipoIngreso() {
    for (const doc of documentos()) {
      for (const s of doc.querySelectorAll('select')) {
        if ([...s.options].some((o) => norm(o.text) === 'planilla excel') && visible(s)) return s;
      }
    }
    return null;
  }

  /** Input que está en la misma fila que la etiqueta `etiqueta`. */
  function campoDe(etiqueta) {
    const e = norm(etiqueta);
    for (const doc of documentos()) {
      for (const el of doc.querySelectorAll('label, td, span, div, th')) {
        const t = norm(el.innerText || el.textContent);
        if (!t.startsWith(e) || t.length > e.length + 6 || !visible(el)) continue;
        let fila = el.closest('tr') || el.parentElement;
        for (let i = 0; fila && i < 3; fila = fila.parentElement, i++) {
          const inp = fila.querySelector('input:not([type=hidden]):not([type=button]):not([type=checkbox]), textarea');
          if (inp) return inp;
        }
      }
    }
    return null;
  }

  function leerSaldo() {
    const c = campoDe('Saldo formularios');
    if (!c) return null;
    const n = parseFloat((c.value || '').replace(/\./g, '').replace(',', '.'));
    return Number.isFinite(n) ? n : null;
  }

  // ---------------------------------------------------------------- archivos

  async function archivoGuardado(i) {
    const a = estado.archivos[i];
    const r = await chrome.storage.local.get(PREF_ARCH + i);
    const dataUrl = r[PREF_ARCH + i];
    if (!dataUrl) throw new Error('No encuentro el contenido del archivo; volvé a elegir la carpeta');
    const [cab, b64] = dataUrl.split(',');
    const tipo = (cab.match(/data:([^;]+)/) || [])[1] || 'application/vnd.ms-excel';
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let k = 0; k < bin.length; k++) bytes[k] = bin.charCodeAt(k);
    return new File([bytes], a.nombre, { type: tipo, lastModified: Date.now() });
  }

  async function elegirArchivos(lista) {
    const files = [...lista]
      .filter((f) => /\.xlsx?$/i.test(f.name) && !f.name.startsWith('~$'))
      .sort((a, b) => a.name.localeCompare(b.name, 'es', { numeric: true }));
    if (!files.length) { alert('No hay archivos .xls / .xlsx en lo que elegiste.'); return; }
    await limpiarArchivos();
    const nuevos = [];
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      const dataUrl = await new Promise((res, rej) => {
        const fr = new FileReader();
        fr.onload = () => res(fr.result);
        fr.onerror = () => rej(fr.error);
        fr.readAsDataURL(f);
      });
      const tipo = f.type || (/\.xlsx$/i.test(f.name)
        ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' : 'application/vnd.ms-excel');
      await chrome.storage.local.set({ [PREF_ARCH + i]: dataUrl.replace(/^data:[^;,]*/, 'data:' + tipo) });
      nuevos.push({ nombre: f.name, tamano: f.size, estado: 'pendiente', vu: '', detalle: '' });
    }
    estado.archivos = nuevos;
    estado.actual = -1;
    estado.paso = '';
    log(`Cargados ${nuevos.length} archivo(s).`);
  }

  async function limpiarArchivos() {
    const todo = await chrome.storage.local.get(null);
    const claves = Object.keys(todo).filter((k) => k.startsWith(PREF_ARCH));
    if (claves.length) await chrome.storage.local.remove(claves);
    estado.archivos = [];
  }

  // ---------------------------------------------------------------- flujo

  async function irAlPaso(p) {
    estado.paso = p;
    await guardar();
    render();
  }

  async function abrirFormulario() {
    const previo = selectTipoIngreso();
    // Solo se aprovecha un formulario ya abierto para el primer archivo (el que abrió el usuario).
    if (previo && estado.reusarFormulario) return;
    estado.reusarFormulario = false;
    let link = null;
    try {
      link = await esperar(() => buscarTexto('Licencia Textiles')[0], 10000, 'el botón "Licencia Textiles"');
    } catch (e) {
      if (e.message === 'DETENIDO' || !estado.urlInicio) {
        throw e.message === 'DETENIDO' ? e : new Error('No encuentro "Licencia Textiles": abrí la página de inicio de DNI');
      }
      if (estado.intentos > 2) throw new Error('No encuentro "Licencia Textiles" en la página de inicio');
      estado.intentos++;
      log('Vuelvo a la página de inicio de DNI…');
      await guardar();
      location.href = estado.urlInicio;
      await sleep(30000);
      throw new Error('La página de inicio no cargó');
    }
    if (!estado.urlInicio) { estado.urlInicio = location.href; await guardar(); }
    clic(link);
    const nuevo = () => { const s = selectTipoIngreso(); return s && s !== previo ? s : null; };
    try {
      await esperar(nuevo, 20000, 'el formulario de solicitud');
    } catch (e) {
      if (e.message === 'DETENIDO') throw e;
      // probar con el ícono que está al lado del texto
      const otra = buscarTexto('Licencia Textiles')[0];
      if (otra && otra.parentElement) clic(otra.parentElement);
      await esperar(nuevo, 30000, 'el formulario de solicitud ("Tipo ingreso")');
    }
  }

  async function elegirPlanillaExcel() {
    const s = await esperar(selectTipoIngreso, 30000, 'la lista "Tipo ingreso"');
    const saldo = leerSaldo();
    if (saldo !== null) {
      log(`Saldo de formularios: ${saldo}`);
      if (saldo < 1) { detener = true; throw new Error('SIN_SALDO'); }
    }
    const op = [...s.options].find((o) => norm(o.text) === 'planilla excel');
    if (s.value !== op.value) {
      s.value = op.value;
      const win = s.ownerDocument.defaultView;
      s.dispatchEvent(new win.Event('input', { bubbles: true }));
      s.dispatchEvent(new win.Event('change', { bubbles: true }));
    }
    await sleep(1200);
  }

  function inputArchivoDocumentos() {
    const nuevo = buscarTexto('Nuevo documento')[0];
    if (!nuevo) return null;
    const doc = nuevo.ownerDocument;
    const ins = [...doc.querySelectorAll('input[type=file]')];
    if (!ins.length) return { nuevo, input: null };
    ins.sort((a, b) => cercania(a, 'Tipo de documento') - cercania(b, 'Tipo de documento'));
    return { nuevo, input: ins[0] };
  }

  function nombreVisibleEnModal(nombre) {
    const n = norm(nombre);
    return documentos().some((doc) => [...doc.querySelectorAll('span, div, a, td, li, label, p')]
      .some((el) => norm(el.innerText || el.textContent).includes(n) && cercania(el, 'Tipo de documento') < 3000 && visible(el)));
  }

  async function subirArchivo(file) {
    const btn = await esperar(() => buscarTexto('Seleccionar archivo')[0], 30000, 'el botón "Seleccionar archivo"');
    clic(btn);
    const r = await esperar(inputArchivoDocumentos, 30000, 'la ventana "Documentos"');
    await sleep(600);
    const win = r.nuevo.ownerDocument.defaultView;
    const dt = new win.DataTransfer();
    dt.items.add(file);
    let entregado = false;
    if (r.input) {
      r.input.files = dt.files;
      r.input.dispatchEvent(new win.Event('input', { bubbles: true }));
      r.input.dispatchEvent(new win.Event('change', { bubbles: true }));
      entregado = true;
    } else {
      // sin <input type=file>: soltar el archivo sobre la zona de subida (nube)
      const doc = r.nuevo.ownerDocument;
      const zonas = [...doc.querySelectorAll('[class*="drop" i], [class*="upload" i], [id*="drop" i], [id*="upload" i]')]
        .filter(visible);
      const celda = r.nuevo.closest('td, div');
      if (celda && celda.nextElementSibling) zonas.push(celda.nextElementSibling);
      for (const z of zonas) {
        for (const ev of ['dragenter', 'dragover', 'drop']) {
          z.dispatchEvent(new win.DragEvent(ev, { bubbles: true, cancelable: true, dataTransfer: dt }));
        }
        entregado = true;
      }
    }
    if (!entregado) throw new Error('No encontré dónde subir el archivo en la ventana "Documentos"');
    await esperar(() => nombreVisibleEnModal(file.name), 60000, `el archivo ${file.name} en la ventana "Documentos"`);
    await sleep(800);
    const conf = await esperar(() => {
      const c = buscarTexto('Confirmar').filter((e) => cercania(e, 'Tipo de documento') < 3000);
      c.sort((a, b) => cercania(a, 'Tipo de documento') - cercania(b, 'Tipo de documento'));
      return c[0];
    }, 15000, 'el "Confirmar" de la ventana "Documentos"');
    clic(conf);
    await esperar(() => !buscarTexto('Nuevo documento').length, 30000, 'que se cierre la ventana "Documentos"');
  }

  async function importar(nombre) {
    await esperar(() => buscarTexto('Descargar archivo').length || buscarTexto(nombre).length, 30000,
      `el archivo ${nombre} junto a "Archivo Excel"`);
    await sleep(500);
    const imp = await esperar(() => buscarTexto('Importar')[0], 15000, 'el botón "Importar"');
    clic(imp);
    await sleep(2500);
    // esperar a que se carguen los datos, o un mensaje de error
    const fin = Date.now() + 90000;
    for (;;) {
      if (detener) throw new Error('DETENIDO');
      const m = leerMensaje();
      if (m) {
        if (m.cerrar) clic(m.cerrar);
        throw new Error('DNI dijo al importar: ' + m.texto.replace(/\s+/g, ' '));
      }
      const ncm = campoDe('Nomenclatura nacional');
      if (ncm && (ncm.value || '').trim()) return { ncm: ncm.value.trim() };
      if (Date.now() > fin) return null;
      await sleep(500);
    }
  }

  function botonConfirmarFinal() {
    const c = buscarTexto('Confirmar').filter((e) => cercania(e, 'Tipo de documento') >= 3000 || !buscarTexto('Nuevo documento').length);
    c.sort((a, b) => {
      const ra = a.getBoundingClientRect(); const rb = b.getBoundingClientRect();
      return rb.width * rb.height - ra.width * ra.height;
    });
    return c[0] || null;
  }

  async function confirmarYLeerNumero(a) {
    const btn = await esperar(botonConfirmarFinal, 20000, 'el botón "Confirmar" del formulario');
    estado.paso = 'enviado';
    await guardar();
    clic(btn);
    log(`${a.nombre}: enviado, esperando el número de trámite…`);
    const m = await esperar(leerMensaje, 180000, 'el mensaje con el número de trámite');
    const num = RE_TRAMITE.exec(m.texto);
    if (m.cerrar) clic(m.cerrar);
    if (!num) throw new Error('DNI respondió: ' + m.texto.replace(/\s+/g, ' '));
    return 'VU' + num[1];
  }

  async function volverAlInicio() {
    await sleep(1000);
    const m = leerMensaje();
    if (m && m.cerrar) { clic(m.cerrar); await sleep(800); }
    if (buscarTexto('Licencia Textiles').length) return;
    // cerrar la pestaña "Ingresar solicitud textiles x"
    const x = buscarTexto('x').concat(buscarTexto('×'))
      .filter((e) => cercania(e, 'Ingresar solicitud textiles') < 120);
    if (x[0]) {
      clic(x[0]);
      try { await esperar(() => buscarTexto('Licencia Textiles').length, 10000, 'inicio'); return; } catch (e) {
        if (e.message === 'DETENIDO') throw e;
      }
    }
    if (estado.urlInicio) {
      log('Vuelvo a la página de inicio de DNI…');
      await guardar();
      location.href = estado.urlInicio;
      await sleep(30000); // la página se recarga y el proceso sigue solo
    }
  }

  function preguntar(texto) {
    estado.pausa = texto;
    guardar();
    render();
    return new Promise((res) => { respuestaPausa = res; });
  }

  async function procesar(i) {
    const a = estado.archivos[i];
    const file = await archivoGuardado(i);

    if (estado.paso === 'inicio') {
      log(`${a.nombre}: abriendo solicitud de licencia textil`);
      await abrirFormulario();
      await irAlPaso('tipo');
    }
    if (estado.paso === 'tipo') {
      await elegirPlanillaExcel();
      await irAlPaso('subir');
    }
    if (estado.paso === 'subir') {
      log(`${a.nombre}: subiendo planilla`);
      await subirArchivo(file);
      await irAlPaso('importar');
    }
    if (estado.paso === 'importar') {
      log(`${a.nombre}: importando`);
      const r = await importar(a.nombre);
      if (r) log(`${a.nombre}: importado (NCM ${r.ncm})`);
      await irAlPaso('pausa');
      if (!r) {
        const resp = await preguntar(`${a.nombre}: no pude comprobar que los datos se importaron. Revisá la pantalla.`);
        if (resp === 'detener') throw new Error('DETENIDO');
        if (resp !== 'continuar') throw new Error('SALTEADO');
      } else if (estado.pausarAntes) {
        const resp = await preguntar(`${a.nombre}: revisá los datos importados y apretá Continuar para enviarlo a DNI.`);
        if (resp === 'detener') throw new Error('DETENIDO');
        if (resp !== 'continuar') throw new Error('SALTEADO');
      }
    }
    if (estado.paso === 'pausa') {
      estado.pausa = null;
      const vu = await confirmarYLeerNumero(a);
      a.estado = 'ok';
      a.vu = vu;
      a.detalle = '';
      log(`${a.nombre}: ${vu}`);
      await irAlPaso('hecho');
    }
  }

  async function correr() {
    if (ejecutando) return;
    ejecutando = true;
    detener = false;
    try {
      while (estado.corriendo) {
        let i = estado.actual;
        if (i < 0 || !estado.archivos[i] || estado.archivos[i].estado !== 'pendiente') {
          i = estado.archivos.findIndex((x) => x.estado === 'pendiente');
          if (i < 0) break;
          estado.actual = i;
          estado.paso = 'inicio';
          estado.intentos = 0;
          await guardar();
        }
        const a = estado.archivos[i];
        try {
          await procesar(i);
        } catch (e) {
          if (e.message === 'DETENIDO') break;
          if (e.message === 'SIN_SALDO') {
            log('No quedan formularios en el saldo de DNI. Me detengo.');
            break;
          }
          if (e.message === 'SALTEADO') {
            a.estado = 'error'; a.detalle = 'Salteado por el usuario (no se envió)';
          } else if (estado.paso === 'enviado') {
            a.estado = 'revisar'; a.detalle = 'Se envió pero no pude leer el número: ' + e.message;
          } else {
            a.estado = 'error'; a.detalle = 'No se envió. ' + e.message;
          }
          log(`${a.nombre}: ${a.detalle}`);
        }
        estado.pausa = null;
        estado.actual = -1;
        await guardar();
        render();
        await volverAlInicio();
      }
    } finally {
      ejecutando = false;
      if (!detener) {
        const ok = estado.archivos.filter((x) => x.estado === 'ok').length;
        if (estado.corriendo && !estado.archivos.some((x) => x.estado === 'pendiente')) {
          log(`Terminado: ${ok} de ${estado.archivos.length} con VU.`);
        }
      }
      estado.corriendo = false;
      estado.pausa = null;
      await guardar();
      render();
    }
  }

  /** Al cargar la página: si había un proceso en marcha, seguirlo. */
  async function reanudar() {
    if (!estado.corriendo) return;
    const a = estado.archivos[estado.actual];
    if (a && a.estado === 'pendiente') {
      if (estado.paso === 'enviado') {
        a.estado = 'revisar';
        a.detalle = 'La página se recargó justo después de enviar: buscá el número en DNI';
        estado.actual = -1;
      } else if (PASOS_ANTES_DE_ENVIAR.includes(estado.paso) && estado.paso !== 'inicio' && estado.paso !== 'tipo') {
        estado.intentos = (estado.intentos || 0) + 1;
        if (estado.intentos > 2) {
          a.estado = 'error'; a.detalle = 'No se envió: la página se recargó varias veces';
          estado.actual = -1;
        } else {
          estado.paso = 'inicio';
        }
      }
    }
    estado.pausa = null;
    await guardar();
    log('Sigo con el proceso…');
    await sleep(2500);
    correr();
  }

  // ---------------------------------------------------------------- resultados

  function filasResultado() {
    return estado.archivos.map((a) => {
      const f = [a.nombre, a.vu || ''];
      if (a.estado !== 'ok') f.push(a.estado === 'pendiente' ? 'Pendiente' : a.detalle);
      return f;
    });
  }

  function descargarPlanilla() {
    if (!estado.archivos.length) return;
    const ws = XLSX.utils.aoa_to_sheet(filasResultado());
    ws['!cols'] = [{ wch: 34 }, { wch: 20 }, { wch: 60 }];
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'Licencias');
    const d = new Date();
    const p = (n) => String(n).padStart(2, '0');
    const nombre = `licencias_textiles_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}.xlsx`;
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
    bajar(new Blob([partes.join('\n\n')], { type: 'text/html' }), 'diagnostico_dni.html');
  }

  // ---------------------------------------------------------------- panel

  let panelHost = null;
  let raiz = null;
  let abierto = false;

  const CSS = `
    :host { all: initial; }
    * { box-sizing: border-box; font-family: Segoe UI, Arial, sans-serif; }
    .fab { position: fixed; right: 18px; bottom: 18px; z-index: 2147483647; background: #1b4f8a; color: #fff;
      border: 0; border-radius: 22px; padding: 10px 16px; font-size: 14px; font-weight: 600; cursor: pointer;
      box-shadow: 0 3px 10px rgba(0,0,0,.3); }
    .panel { position: fixed; right: 18px; bottom: 70px; z-index: 2147483647; width: 460px; max-height: 80vh;
      display: flex; flex-direction: column; background: #fff; color: #222; border: 1px solid #b8c4d4; border-radius: 8px;
      box-shadow: 0 6px 24px rgba(0,0,0,.25); font-size: 13px; }
    .cab { background: #1b4f8a; color: #fff; padding: 8px 12px; font-weight: 600; display: flex; justify-content: space-between;
      border-radius: 8px 8px 0 0; }
    .cab button { background: none; border: 0; color: #fff; font-size: 16px; cursor: pointer; }
    .cuerpo { padding: 10px 12px; overflow: auto; }
    .fila { display: flex; gap: 6px; flex-wrap: wrap; margin: 6px 0; }
    button.b { border: 1px solid #1b4f8a; background: #fff; color: #1b4f8a; border-radius: 4px; padding: 5px 10px;
      font-size: 13px; cursor: pointer; }
    button.b.p { background: #1b4f8a; color: #fff; }
    button.b:disabled { opacity: .45; cursor: default; }
    table { width: 100%; border-collapse: collapse; margin: 6px 0; }
    th, td { text-align: left; padding: 3px 4px; border-bottom: 1px solid #e3e8ef; vertical-align: top; }
    th { font-size: 12px; color: #555; }
    .ok { color: #17803d; font-weight: 600; } .error { color: #b42318; } .revisar { color: #b54708; font-weight: 600; }
    .act { background: #eef4fb; }
    .det { font-size: 11px; color: #666; }
    .pausa { background: #fff6e0; border: 1px solid #f0c36d; border-radius: 6px; padding: 8px; margin: 6px 0; }
    .log { font-family: Consolas, monospace; font-size: 11px; background: #f6f8fa; padding: 6px; height: 110px;
      overflow: auto; white-space: pre-wrap; border-radius: 4px; }
    .nota { font-size: 11px; color: #666; margin: 4px 0; }
    label.chk { display: flex; gap: 6px; align-items: center; margin: 6px 0; }
    input[type=file] { display: none; }
  `;

  function crearPanel() {
    panelHost = document.createElement('div');
    panelHost.id = 'elgonza-licencias';
    raiz = panelHost.attachShadow({ mode: 'open' });
    raiz.innerHTML = `<style>${CSS}</style><button class="fab" id="fab">Licencias textiles</button><div id="pan"></div>`;
    document.documentElement.appendChild(panelHost);
    raiz.getElementById('fab').addEventListener('click', () => { abierto = !abierto; render(); });
  }

  const esc = (s) => String(s || '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function render() {
    if (!raiz) return;
    const pan = raiz.getElementById('pan');
    if (estado.corriendo || estado.pausa) abierto = true;
    if (!abierto) { pan.innerHTML = ''; return; }
    const ok = estado.archivos.filter((a) => a.estado === 'ok').length;
    const pend = estado.archivos.filter((a) => a.estado === 'pendiente').length;
    const filas = estado.archivos.map((a, i) => `
      <tr class="${i === estado.actual ? 'act' : ''}">
        <td>${i + 1}</td>
        <td>${esc(a.nombre)}${a.detalle ? `<div class="det">${esc(a.detalle)}</div>` : ''}</td>
        <td class="${a.estado}">${a.estado === 'ok' ? esc(a.vu) : a.estado === 'pendiente'
          ? (i === estado.actual ? (estado.pausa ? 'Esperando' : 'Trabajando…') : '—') : a.estado === 'revisar' ? 'Revisar' : 'Error'}</td>
      </tr>`).join('');
    pan.innerHTML = `
      <div class="panel">
        <div class="cab"><span>Licencias textiles · DNI</span><button id="cerrarPanel" title="Ocultar">×</button></div>
        <div class="cuerpo">
          <div class="fila">
            <button class="b" id="bCarpeta" ${estado.corriendo ? 'disabled' : ''}>Elegir carpeta</button>
            <button class="b" id="bArchivos" ${estado.corriendo ? 'disabled' : ''}>Elegir archivos</button>
            <input type="file" id="iCarpeta" webkitdirectory multiple>
            <input type="file" id="iArchivos" multiple accept=".xls,.xlsx">
          </div>
          ${estado.archivos.length ? `<table><tr><th>#</th><th>Archivo</th><th>VU</th></tr>${filas}</table>
            <div class="nota">${ok} con VU · ${pend} pendiente(s) · ${estado.archivos.length} en total</div>`
            : '<div class="nota">Elegí la carpeta con las planillas (.xls / .xlsx) de licencia textil.</div>'}
          ${estado.pausa ? `<div class="pausa">${esc(estado.pausa)}
            <div class="fila"><button class="b p" id="bSeguir">Continuar (enviar)</button>
            <button class="b" id="bSaltear">Saltear este</button></div></div>` : ''}
          <label class="chk"><input type="checkbox" id="cPausa" ${estado.pausarAntes ? 'checked' : ''}>
            Parar antes de enviar cada uno, para revisar los datos</label>
          <div class="fila">
            <button class="b p" id="bIniciar" ${estado.corriendo || !pend ? 'disabled' : ''}>Iniciar</button>
            <button class="b" id="bDetener" ${estado.corriendo ? '' : 'disabled'}>Detener</button>
            <button class="b" id="bBajar" ${estado.archivos.length ? '' : 'disabled'}>Descargar planilla</button>
            <button class="b" id="bCopiar" ${estado.archivos.length ? '' : 'disabled'}>Copiar</button>
          </div>
          <div class="nota">Cada envío usa un formulario del saldo de DNI. Dejá esta pestaña abierta mientras trabaja.</div>
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
    $('bCarpeta').onclick = () => $('iCarpeta').click();
    $('bArchivos').onclick = () => $('iArchivos').click();
    $('iCarpeta').onchange = (e) => elegirArchivos(e.target.files);
    $('iArchivos').onchange = (e) => elegirArchivos(e.target.files);
    $('cPausa').onchange = (e) => { estado.pausarAntes = e.target.checked; guardar(); };
    $('bIniciar').onclick = () => {
      const pend2 = estado.archivos.filter((a) => a.estado === 'pendiente').length;
      if (!confirm(`Se van a enviar ${pend2} solicitud(es) de licencia textil a DNI. ¿Seguimos?`)) return;
      estado.corriendo = true;
      estado.actual = -1;
      estado.reusarFormulario = true;
      if (buscarTexto('Licencia Textiles').length) estado.urlInicio = location.href;
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
      if (estado.archivos.length && !confirm('¿Vaciar la lista y los resultados?')) return;
      await limpiarArchivos(); estado.log = []; estado.actual = -1; await guardar(); render();
    };
    $('bDiag').onclick = diagnostico;
  }

  // ---------------------------------------------------------------- arranque

  (async () => {
    await cargar();
    crearPanel();
    render();
    reanudar();
  })();
})();
