"""Punto de entrada: línea de comandos y ventana para elegir la carpeta."""
import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__
from .borrador import extraer_texto_pdf, parsear
from .documentos import EXTENSIONES, cargar_carpeta
from .mega6 import borrador_de_carpeta, raiz_mega6, ultimo_borrador
from .informe import a_json, generar
from .reglas import Revision


def carpeta_informes():
    d = Path.home() / "Documents" / "elGonza" / "informes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def revisar_carpeta(carpeta, salida=None, usar_ocr=True, avisar=print, mega6=None):
    """Revisa una carpeta de despacho (o un PDF de borrador). Devuelve (ruta_informe, controles) o (None, mensaje).

    mega6: True para tomar los documentos de Mega6, False para no usarlo. Sin indicar, se usa Mega6 cuando la
    carpeta tiene solo el borrador.
    """
    carpeta = Path(carpeta)
    if carpeta.is_file():
        ruta_borrador, carpeta, mega6 = carpeta, carpeta.parent, True if mega6 is None else mega6
    else:
        ruta_borrador = next((p for p in sorted(carpeta.iterdir()) if p.is_file()
                              and p.suffix.lower() == ".pdf" and p.name.lower().startswith("borrador")), None)
    if not ruta_borrador:
        return None, f"No encontré un archivo «Borrador…» en {carpeta}"
    avisar(f"Leyendo {ruta_borrador.name}…")
    b = parsear(extraer_texto_pdf(ruta_borrador))
    if not b.carpeta:
        b.carpeta = "".join(c for c in ruta_borrador.stem if c.isdigit()) or carpeta.name
    if mega6 is None:
        otros = [p for p in carpeta.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONES and p != ruta_borrador]
        mega6 = not otros and raiz_mega6() is not None
    if mega6 and raiz_mega6() is None:
        return None, "No encontré la base de Mega6 (Permiso6). Indicá la ruta en la variable ELGONZA_MEGA6."
    _, docs = cargar_carpeta(carpeta, usar_ocr=usar_ocr, avisar=avisar, mega6=b.carpeta if mega6 else None)
    if mega6 and not docs:
        return None, f"Mega6 no tiene documentos RADE para la carpeta {b.carpeta}"
    controles = Revision(b, docs).ejecutar()
    try:
        from . import historial
        n = historial.aprender(b, docs)
        if n:
            avisar(f"Aprendí {n} código(s) de producto con su NCM para sugerirlos en próximas facturas.")
    except Exception:
        pass
    salida = Path(salida) if salida else carpeta_informes()
    salida.mkdir(parents=True, exist_ok=True)
    ruta = salida / f"revision-{b.carpeta}.html"
    ruta.write_text(generar(b, docs, controles, carpeta), encoding="utf-8")
    (salida / f"revision-{b.carpeta}.json").write_text(a_json(b, controles), encoding="utf-8")
    return ruta, controles


def carpetas_a_revisar(raiz):
    """La carpeta elegida, o sus subcarpetas si es una carpeta de carpetas."""
    raiz = Path(raiz)
    if any(p.name.lower().startswith("borrador") for p in raiz.iterdir() if p.is_file()):
        return [raiz]
    return sorted(p for p in raiz.iterdir() if p.is_dir() and any(
        q.name.lower().startswith("borrador") for q in p.iterdir() if q.is_file()))


def resumen(controles):
    n = {k: sum(1 for c in controles if c.resultado == k) for k in ("error", "advertencia", "revisar")}
    return f"{n['error']} errores, {n['advertencia']} advertencias, {n['revisar']} a revisar"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="elgonza", description="Revisa el borrador de DUA contra los documentos de la carpeta.")
    ap.add_argument("carpeta", nargs="?", help="Carpeta del despacho (o carpeta con varias carpetas de despacho)")
    ap.add_argument("--salida", help="Carpeta donde guardar los informes (por defecto Documentos\\elGonza\\informes)")
    ap.add_argument("--sin-ocr", action="store_true", help="No usar OCR en documentos escaneados")
    ap.add_argument("--no-abrir", action="store_true", help="No abrir el informe en el navegador")
    ap.add_argument("--mega6", action="store_true", help="Tomar los documentos de Mega6 aunque la carpeta tenga archivos")
    ap.add_argument("--carpeta", dest="nro", help="Número de carpeta: toma el borrador que generó Mega6 (respaldo\\Borrador<nro>.pdf) y sus documentos")
    ap.add_argument("--planilla", metavar="CARPETA", help="Factura a planilla: arma la planilla de ítems con NCM sugerido "
                    "a partir de las facturas y packing lists de la carpeta (paso previo al borrador)")
    ap.add_argument("--formato", choices=["xlsx", "ods", "ambos"],
                    help="Formato de la planilla: xlsx (Excel), ods (OpenOffice) o ambos")
    ap.add_argument("--antecedentes", action="store_true",
                    help="Arma o actualiza la base de antecedentes de NCM con los borradores de Mega6")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)
    if args.antecedentes:
        from . import historial
        n = historial.actualizar(avisar=print)
        print(f"Base de antecedentes: {n} borradores leídos -> {historial.ruta_base()}")
        return 0
    if args.planilla:
        from .planilla import planilla_de_carpeta
        ruta, res = planilla_de_carpeta(args.planilla, args.salida and Path(args.salida) / f"Planilla {Path(args.planilla).name}.xlsx",
                                        not args.sin_ocr, formato=args.formato)
        if ruta is None:
            print(res)
            return 1
        print(f"{sum(len(f.lineas) for f in res)} ítems de {len(res)} factura(s) -> {ruta}")
        if not args.no_abrir:
            abrir(ruta)
        return 0
    if args.nro:
        args.carpeta = borrador_de_carpeta(args.nro)
        if not args.carpeta:
            print(f"No encontré el borrador de la carpeta {args.nro} en Mega6 (respaldo\\Borrador{args.nro}.pdf)")
            return 1
    if not args.carpeta:
        return ventana()
    carpetas = [Path(args.carpeta)] if Path(args.carpeta).is_file() else carpetas_a_revisar(args.carpeta)
    if not carpetas:
        print(f"No encontré borradores en {args.carpeta}")
        return 1
    for c in carpetas:
        ruta, controles = revisar_carpeta(c, args.salida, not args.sin_ocr, mega6=True if args.mega6 else None)
        if ruta is None:
            print(controles)
            continue
        print(f"{c.name}: {resumen(controles)} -> {ruta}")
        if not args.no_abrir and len(carpetas) == 1:
            webbrowser.open(ruta.as_uri())
    return 0


def abrir(ruta):
    """Abre el archivo con su programa (Excel, OpenOffice, navegador). En Windows se lo pide al Explorador para que
    Excel no quede como «hijo» de elGonza: si no, algunos antivirus le atribuyen a elGonza el guardado de Excel y lo
    bloquean, y se pierden los cambios hechos a mano."""
    if sys.platform == "win32":
        import subprocess
        try:
            subprocess.Popen(["explorer.exe", str(Path(ruta).resolve())], close_fds=True,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            return
        except OSError:
            os.startfile(str(ruta))
            return
    webbrowser.open(Path(ruta).as_uri())


def ventana():
    import tkinter as tk
    from tkinter import filedialog, scrolledtext

    raiz = tk.Tk()
    raiz.title(f"elGonza · revisión de DUA {__version__}")
    raiz.geometry("720x460")
    marco = tk.Frame(raiz, padx=12, pady=12)
    marco.pack(fill="both", expand=True)
    tk.Label(marco, text="Elegí la carpeta del despacho (o una carpeta con varias), o solo el borrador para traer los documentos de Mega6.", anchor="w").pack(fill="x")
    log = scrolledtext.ScrolledText(marco, height=18, state="disabled", font=("Consolas", 10))
    informes = []

    def escribir(msg):
        def _():
            log.configure(state="normal")
            log.insert("end", msg + "\n")
            log.see("end")
            log.configure(state="disabled")
        raiz.after(0, _)

    def trabajar(carpeta):
        try:
            carpetas = [Path(carpeta)] if Path(carpeta).is_file() else carpetas_a_revisar(carpeta)
            if not carpetas:
                escribir("No encontré ningún archivo «Borrador…» en esa carpeta.")
            for c in carpetas:
                escribir(f"— Revisando {c.stem if c.is_file() else c.name}")
                ruta, controles = revisar_carpeta(c, avisar=escribir)
                if ruta is None:
                    escribir(controles)
                    continue
                informes.append(ruta)
                escribir(f"  {resumen(controles)}")
                escribir(f"  Informe: {ruta}")
            if len(informes) == 1:
                webbrowser.open(informes[0].as_uri())
            elif informes:
                abrir(informes[0].parent)
        except Exception as e:
            escribir(f"Error: {e}")
        finally:
            raiz.after(0, lambda: boton.configure(state="normal"))

    def elegir():
        carpeta = filedialog.askdirectory(title="Carpeta del despacho")
        if not carpeta:
            return
        informes.clear()
        boton.configure(state="disabled")
        threading.Thread(target=trabajar, args=(carpeta,), daemon=True).start()

    def elegir_borrador():
        archivo = filedialog.askopenfilename(title="Borrador del DUA", filetypes=[("PDF", "*.pdf")])
        if not archivo:
            return
        if raiz_mega6() is None:
            escribir("No encontré la base de Mega6 (Permiso6).")
            return
        informes.clear()
        boton.configure(state="disabled")
        threading.Thread(target=trabajar, args=(archivo,), daemon=True).start()

    def carpeta_mega6():
        from tkinter import simpledialog
        if raiz_mega6() is None:
            escribir("No encontré la base de Mega6 (Permiso6).")
            return
        ultimo = ultimo_borrador()
        sugerido = ultimo.stem.replace("Borrador", "") if ultimo else ""
        nro = simpledialog.askstring("Carpeta de Mega6", "Número de carpeta (el borrador tiene que estar generado en Mega6):",
                                     initialvalue=sugerido, parent=raiz)
        if not nro:
            return
        archivo = borrador_de_carpeta(nro.strip())
        if not archivo:
            escribir(f"No encontré el borrador de la carpeta {nro.strip()}: generalo en Mega6 y volvé a probar.")
            return
        informes.clear()
        boton.configure(state="disabled")
        threading.Thread(target=trabajar, args=(str(archivo),), daemon=True).start()

    def trabajar_planilla(carpeta):
        from .planilla import planilla_de_carpeta
        try:
            escribir(f"— Factura a planilla: {Path(carpeta).name}")
            ruta, res = planilla_de_carpeta(carpeta, avisar=escribir, formato=formato.get())
            if ruta is None:
                escribir(res)
                return
            for f in res:
                escribir(f"  Factura {f.numero or f.archivo} ({f.fecha or 'sin fecha'}): {len(f.lineas)} ítems")
                for o in f.observaciones:
                    escribir(f"    · {o}")
            escribir(f"  Planilla: {ruta}")
            abrir(ruta)
        except PermissionError:
            escribir("No pude guardar la planilla: ¿está abierta en Excel u OpenOffice? Cerrala y volvé a probar.")
        except Exception as e:
            escribir(f"Error: {e}")
        finally:
            raiz.after(0, lambda: boton.configure(state="normal"))

    def elegir_planilla():
        carpeta = filedialog.askdirectory(title="Carpeta con las facturas (y packing lists)")
        if not carpeta:
            return
        boton.configure(state="disabled")
        threading.Thread(target=trabajar_planilla, args=(carpeta,), daemon=True).start()

    def abrir_libro():
        from .clientes import cargar, ruta_libro
        cargar()  # lo crea con ejemplos si no existe
        ruta = ruta_libro()
        abrir(ruta)

    botones = tk.Frame(marco)
    botones.pack(anchor="w", pady=8, fill="x")
    boton = tk.Button(botones, text="Elegir carpeta y revisar…", command=elegir, padx=10, pady=4)
    boton.pack(side="left")
    tk.Button(botones, text="Carpeta de Mega6…", command=carpeta_mega6, padx=10, pady=4).pack(side="left", padx=8)
    tk.Button(botones, text="Borrador + Mega6…", command=elegir_borrador, padx=10, pady=4).pack(side="left")
    tk.Button(botones, text="Libro de clientes…", command=abrir_libro, padx=10, pady=4).pack(side="left", padx=8)
    previo = tk.Frame(marco)
    previo.pack(anchor="w", pady=(0, 8), fill="x")
    tk.Label(previo, text="Antes del borrador:").pack(side="left")
    tk.Button(previo, text="Factura a planilla…", command=elegir_planilla, padx=10, pady=4).pack(side="left", padx=8)
    from .planilla import formato_preferido, guardar_formato
    nombres = {"xlsx": "Excel (.xlsx)", "ods": "OpenOffice (.ods)", "ambos": "Excel y OpenOffice"}
    formato = tk.StringVar(value=formato_preferido())
    etiqueta = tk.StringVar(value=nombres[formato.get()])

    def cambiar_formato(nombre):
        f = next(k for k, v in nombres.items() if v == nombre)
        formato.set(f)
        guardar_formato(f)
    tk.OptionMenu(previo, etiqueta, *nombres.values(), command=cambiar_formato).pack(side="left", padx=(0, 8))

    def trabajar_base():
        from . import historial
        try:
            n = historial.actualizar(avisar=escribir)
            if n == 0 and not historial.existe():
                escribir("No encontré borradores en Permiso6\\respaldo. Indicá la ruta de Mega6 en ELGONZA_MEGA6.")
            else:
                escribir(f"Base de antecedentes al día ({n} borradores nuevos leídos). La uso al sugerir el NCM.")
        except Exception as e:
            escribir(f"Error armando la base de antecedentes: {e}")
        finally:
            raiz.after(0, lambda: boton.configure(state="normal"))

    def armar_base():
        boton.configure(state="disabled")
        threading.Thread(target=trabajar_base, daemon=True).start()

    def abrir_antecedentes():
        from .ncm import antecedentes, ruta_antecedentes
        antecedentes()  # lo crea con ejemplos si no existe
        abrir(ruta_antecedentes())
    tk.Button(previo, text="Antecedentes NCM…", command=abrir_antecedentes, padx=10, pady=4).pack(side="left")
    tk.Button(previo, text="Base de antecedentes (Mega6)", command=armar_base, padx=10, pady=4).pack(side="left",
                                                                                                    padx=8)
    log.pack(fill="both", expand=True)
    raiz.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
