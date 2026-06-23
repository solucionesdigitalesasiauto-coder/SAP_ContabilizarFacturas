"""ZFIEC015 — Recepción de Documentos Electrónicos (navegación por teclado + scripting)."""
import os
import sys
import time
import logging

from pynput.keyboard import Controller as _KbCtrl, Key as _Key

import sap_gui as SAP

_log = logging.getLogger(__name__)

# ── Tab-counts calibrados con Au3Info (17-18/06/2026) ────────
_TAB_PROVEEDOR   = 1   # desde campo Sociedad
_TAB_FECHA_DESDE = 9
_TAB_FECHA_HASTA = 1
_TAB_TIPO_DOC    = 5
_TAB_PENDIENTE   = 4
_kbd = _KbCtrl()

# campos.py contiene los IDs de elementos SAP para scripting (opcional)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "diagnostico"))
try:
    from campos import (
        ZFIEC_SOCIEDAD,    ZFIEC_SOCIEDAD_ALT,
        ZFIEC_PROVEEDOR,   ZFIEC_PROVEEDOR_ALT,
        ZFIEC_FECHA_DESDE, ZFIEC_FECHA_DESDE_ALT,
        ZFIEC_FECHA_HASTA, ZFIEC_FECHA_HASTA_ALT,
        ZFIEC_TIPO_DOC,    ZFIEC_TIPO_DOC_ALT,
        ZFIEC_RADIO_PENDIENTE, ZFIEC_RADIO_PEND_ALT1, ZFIEC_RADIO_PEND_ALT2,
    )
    _CAMPOS_DISPONIBLES = True
except ImportError:
    _CAMPOS_DISPONIBLES = False
    _log.debug("campos.py no disponible — scripting de formulario deshabilitado.")


# ── Sesión SAP Scripting ──────────────────────────────────────

def _get_session():
    """Retorna la sesión SAP Scripting activa o None si no está disponible."""
    try:
        import win32com.client
        app     = win32com.client.GetObject("SAPGUI").GetScriptingEngine
        session = app.Children(0).Children(0)
        _       = session.Type   # valida que la sesión esté activa
        return session
    except Exception as e:
        _log.debug("SAP Scripting no disponible: %s", e)
        return None


# ── PASO 1: Llenar formulario ZFIEC015 ───────────────────────

def buscar(proveedor: str, fecha_desde: str, fecha_hasta: str,
           sociedad: str = None, tipo_doc: str = None) -> int:
    """Navega a ZFIEC015, llena el formulario y ejecuta la búsqueda (F8).

    Returns:
        Número de filas encontradas (0 = sin pendientes, -1 = hay datos pero sin scripting).
    """
    sociedad = sociedad or os.getenv("SAP_SOCIEDAD", "")
    tipo_doc = tipo_doc or os.getenv("TIPO_DOC_ZFIEC", "")

    SAP.ir_a(os.getenv("TCODE_ZFIEC015", "ZFIEC015"))
    SAP.esperar_titulo("Recepcion de documentos Electronicos", timeout=20)
    SAP.verificar_pantalla("Recepcion de documentos Electronicos", "ZFIEC015-Formulario")
    time.sleep(0.5)

    session = _get_session()
    if session and _CAMPOS_DISPONIBLES:
        _llenar_form_scripting(session, sociedad, proveedor, fecha_desde, fecha_hasta, tipo_doc)
    else:
        _llenar_form_teclado(sociedad, proveedor, fecha_desde, fecha_hasta, tipo_doc)
    time.sleep(2.5)

    titulo = SAP.titulo_actual()
    _log.debug("ZFIEC015 post-F8: %r", titulo)

    if "recepci" not in titulo.lower() and "electr" not in titulo.lower():
        _log.info("Sin resultados en ZFIEC015 para proveedor %s", proveedor)
        return 0

    if session:
        try:
            grid = session.findById("wnd[0]/usr/cntlGRID1/shellcont/shell")
            filas = grid.RowCount
            _log.info("ZFIEC015: %d filas encontradas", filas)
            return filas
        except Exception as e:
            _log.debug("No se pudo leer RowCount via scripting: %s", e)

    return -1   # hay datos pero no se puede contar sin scripting


def _llenar_form_scripting(session, sociedad, proveedor,
                            fecha_desde, fecha_hasta, tipo_doc) -> None:
    _set_field(session, ZFIEC_SOCIEDAD,    ZFIEC_SOCIEDAD_ALT,    value=sociedad)
    _set_field(session, ZFIEC_PROVEEDOR,   ZFIEC_PROVEEDOR_ALT,   value=proveedor)
    _set_field(session, ZFIEC_FECHA_DESDE, ZFIEC_FECHA_DESDE_ALT, value=fecha_desde)
    _set_field(session, ZFIEC_FECHA_HASTA, ZFIEC_FECHA_HASTA_ALT, value=fecha_hasta)
    _set_field(session, ZFIEC_TIPO_DOC,    ZFIEC_TIPO_DOC_ALT,    value=tipo_doc)

    for radio_id in (ZFIEC_RADIO_PENDIENTE, ZFIEC_RADIO_PEND_ALT1, ZFIEC_RADIO_PEND_ALT2):
        try:
            session.findById(radio_id).select()
            break
        except Exception:
            continue

    session.findById("wnd[0]").sendVKey(8)   # F8 = Ejecutar
    _log.debug("ZFIEC015 formulario enviado via scripting.")


def _set_field(session, *ids, value: str) -> bool:
    """Escribe en un campo SAP probando IDs alternativos."""
    for fid in ids:
        try:
            session.findById(fid).text = value
            return True
        except Exception:
            continue
    _log.warning("No se encontró campo SAP para IDs: %s", ids)
    return False


def _llenar_form_teclado(sociedad, proveedor, fecha_desde, fecha_hasta, tipo_doc) -> None:
    """Fallback: llena ZFIEC015 navegando por teclado."""
    SAP.activar("Recepción de documentos Electrónicos")
    time.sleep(0.3)
    SAP.campo_ctrlA(sociedad)
    SAP.activar()
    time.sleep(0.3)

    SAP.tab(_TAB_PROVEEDOR)
    time.sleep(0.3)
    SAP.escribir(proveedor)
    time.sleep(0.2)

    SAP.activar()
    SAP.tecla('down')
    time.sleep(0.1)
    SAP.tecla('down')
    time.sleep(0.1)
    SAP.tecla('down')
    time.sleep(0.3)
    SAP.escribir(fecha_desde)
    time.sleep(0.2)

    SAP.tab(_TAB_FECHA_HASTA)
    time.sleep(0.3)
    SAP.escribir(fecha_hasta)
    time.sleep(0.2)

    SAP.tab(_TAB_TIPO_DOC)
    time.sleep(0.3)
    SAP.escribir(tipo_doc)
    time.sleep(0.2)

    SAP.tab(_TAB_PENDIENTE)
    time.sleep(0.2)
    SAP.tecla('space')

    SAP.activar("Recepción de documentos Electrónicos")
    time.sleep(0.3)
    SAP.f8()
    _log.debug("ZFIEC015 formulario enviado via teclado.")


# ── PASO 2: Procesar filas de la grilla ──────────────────────

_detener = False


def _iniciar_listener_parada():
    """Escucha ESC en segundo plano para detener el proceso limpiamente."""
    from pynput import keyboard as _kb_mod

    def _on_press(key):
        global _detener
        if key == _kb_mod.Key.esc:
            _detener = True
            print("\n  [!] ESC — deteniendo al terminar el documento actual...")
            return False

    listener = _kb_mod.Listener(on_press=_on_press)
    listener.daemon = True
    listener.start()
    return listener


def procesar_documentos(banco: dict,
                        proveedor: str = None,
                        fecha_desde: str = None,
                        fecha_hasta: str = None,
                        sociedad: str = None,
                        tipo_doc: str = None,
                        max_docs: int = None):
    """Itera la grilla ZFIEC015 y registra cada factura en FB60.

    Avance lineal: fila_idx sube tras cada documento (sin re-query).
    El loop termina cuando _abrir_fb60_teclado devuelve False (grilla vacía).

    Returns:
        Tupla (lista_procesados, lista_errores).
    """
    global _detener
    _detener = False
    from transactions.fb60_kb import registrar_factura
    listener       = _iniciar_listener_parada()
    procesados     = []
    errores        = []
    errores_consec = 0
    max_errores    = 3
    fila_idx       = 0
    _log.info("Procesando grilla ZFIEC015 en modo teclado")
    print("    Modo grilla: teclado  |  ESC para detener.")
    while True:
        if _detener:
            print("    Proceso detenido por el usuario.")
            break
        try:
            SAP.activar("Recepción de documentos Electrónicos")
            SAP.posicionar_ventana()
        except RuntimeError:
            _log.info("Grilla ZFIEC015 cerrada — fin del banco.")
            break
        doc_id = f"fila_{fila_idx + 1}"
        _log.debug("Abriendo %s", doc_id)
        if not _abrir_fb60_teclado(fila_idx):
            _log.info("Sin más documentos en grilla (fila %d).", fila_idx + 1)
            break
        try:
            resultado = registrar_factura(banco)
            procesados.append({"doc": doc_id, **resultado})
            print(f"    ✓ Doc SAP: {resultado['sap_doc']}")
            _log.info("Procesado %s → Doc SAP: %s", doc_id, resultado['sap_doc'])
            errores_consec = 0
            _cerrar_fb60_si_abierto()
            time.sleep(0.5)
            fila_idx += 1
            if max_docs and len(procesados) >= max_docs:
                _log.info("Límite max_docs=%d alcanzado.", max_docs)
                break
        except Exception as e:
            _log.error("Error FB60 en %s: %s", doc_id, e, exc_info=True)
            print(f"    ✗ {doc_id}: {e}")
            errores.append({"doc": doc_id, "error": str(e)})
            errores_consec += 1
            _cerrar_fb60_si_abierto()
            fila_idx += 1
            time.sleep(1.0)
            if errores_consec >= max_errores:
                _log.error("Demasiados errores consecutivos — deteniendo banco.")
                break
    try:
        listener.stop()
    except Exception:
        pass
    _cerrar_fb60_si_abierto()
    return procesados, errores

def _abrir_fb60_scripting(session, fila_idx: int) -> bool:
    """Abre FB60 asegurando la re-identificación de la grilla."""
    try:
        # 1. SIEMPRE re-obtener el objeto Grid desde la sesión activa
        # Esto soluciona que el clic "no haga nada" después de la primera vez
        grid = session.findById("wnd[0]/usr/cntlGRID1/shellcont/shell")
        
        # 2. Validar que la fila existe
        if fila_idx >= grid.RowCount:
            return False

        # 3. Hacer foco en la celda antes de presionar el botón
        # Esto garantiza que SAP sepa sobre qué fila estamos trabajando
        grid.setCurrentCell(fila_idx, "FB60") 
        grid.pressButton(fila_idx, "FB60")
        
        time.sleep(1.0) # Tiempo para que cargue la ventana de FB60

        # 4. Manejo del popup de confirmación
        try:
            # Si aparece un mensaje, lo aceptamos
            if session.ActiveWindow.Name == "wnd[1]":
                session.findById("wnd[1]/tbar[0]/btn[0]").press()
                time.sleep(0.5)
        except:
            pass

        return True

    except Exception as e:
        _log.warning("Fallo al interactuar con el Grid en fila %d: %s", fila_idx, e)
        return False


def _abrir_fb60_teclado(fila_idx: int) -> bool:
    """Fallback: abre FB60 navegando la grilla con teclado."""
    SAP.activar("Recepcion de documentos Electronicos")
    time.sleep(0.8)
    if fila_idx == 0:
        # Grid recién cargado: F2 despierta el foco de teclado sin abrir transacción
        _kbd.press(_Key.f2); _kbd.release(_Key.f2)
        time.sleep(0.5)
    else:
        # Grid ya activo: solo avanzar a la siguiente fila
        _kbd.press(_Key.down); _kbd.release(_Key.down)
        time.sleep(0.3)
    # Home → MIRO (primera col interactiva), Right → FB60
    _kbd.press(_Key.home); _kbd.release(_Key.home)
    time.sleep(0.3)
    _kbd.press(_Key.right); _kbd.release(_Key.right)
    time.sleep(0.2)
    _kbd.press(_Key.enter); _kbd.release(_Key.enter)   # abre popup de confirmación
    time.sleep(1.5)
    _kbd.press(_Key.enter); _kbd.release(_Key.enter)   # confirma popup → Sí
    try:
        SAP.esperar_titulo("Registrar factura", timeout=3)
        return True
    except RuntimeError:
        _log.debug("FB60 no se abrió (grilla vacía o timeout). Pantalla: %r", SAP.titulo_actual())
        return False
    
    
def _cerrar_fb60_si_abierto() -> None:
    """Cierra FB60 si sigue abierto, para poder continuar con el siguiente banco."""
    titulo = SAP.titulo_actual()
    if not any(p in titulo.lower() for p in ("registrar factura", "ingresar factura")):
        return
    _log.debug("Cerrando FB60 residual antes de continuar.")
    SAP.activar()
    SAP.f12()
    time.sleep(0.8)
    titulo2 = SAP.titulo_actual().lower()
    if any(p in titulo2 for p in ("registrar factura", "ingresar factura", "tratamiento")):
        SAP.enter()   # Sí tiene foco por defecto
        time.sleep(0.8)
