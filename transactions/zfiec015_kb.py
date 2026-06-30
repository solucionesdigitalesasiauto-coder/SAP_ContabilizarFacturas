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
_kbd = _KbCtrl()

# ── Timings (ajustar si SAP responde más lento) ───────────────
_SLEEP_CORTO      = 0.3   # entre teclas en formulario
_SLEEP_MEDIO      = 0.6   # entre pasos
_SLEEP_LARGO      = 1.5   # pausa larga (F2, Home, Right en grilla)
_SLEEP_POPUP      = 2.5   # espera popup HTML de confirmación
_SLEEP_CARGA      = 2.5   # espera carga de resultados F8
_SLEEP_REINTENTAR = 2.0   # reintento de apertura FB60
_SLEEP_ENTRE_DOCS = 1.0   # pausa entre documentos consecutivos

# ── Timeouts pywinauto (ajustar si el sistema es más lento) ───
_TIMEOUT_POPUP_CONFIRM  = 2.0   # segundos esperando popup HTML de confirmación grilla
_TIMEOUT_CONNECT        = 0.5   # timeout pywinauto connect (sondeo rápido en loop)
_TIMEOUT_CONNECT_BARRA  = 1.0   # timeout pywinauto connect en _leer_barra_zfiec
_TIMEOUT_PANE_EXISTS    = 0.1   # timeout exists() del pane popup FB60
_TIMEOUT_BTN_EXISTS     = 0.3   # timeout exists() del botón Sí

# ── Títulos y strings SAP ─────────────────────────────────────
_TITULO_ZFIEC         = "Recepcion de documentos Electronicos"   # sin tilde (esperar_titulo)
_TITULO_ZFIEC_ES      = "Recepción de documentos Electrónicos"   # con tilde (activar)
_TITULO_FB60          = "Registrar factura"
_GRID_SAP_ID          = "wnd[0]/usr/cntlGRID1/shellcont/shell"
_COL_FB60             = "FB60"                                   # columna de botón en grilla
_TIMEOUT_ZFIEC        = 15   # segundos esperando pantalla ZFIEC015
_TIMEOUT_FB60         = 5    # segundos esperando apertura de FB60
_MAX_INTENTOS_POPUP   = 3    # reintentos Enter para popup HTML de ZFIEC015

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
    """Obtiene la sesión SAP Scripting activa vía win32com.

    Intenta conectarse al motor de scripting de SAP GUI.
    Si SAP Scripting no está habilitado o SAP no está abierto, retorna None.

    Returns:
        object | None: Objeto session de SAP Scripting, o None si no disponible.

    Hardcoded:
        - "SAPGUI": nombre del objeto COM de SAP (STRING — fijo en SAP GUI)
        - Children(0).Children(0): índices de conexión y sesión (NÚMERO MÁGICO)
    """
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

def _validar_campos_zfiec(proveedor, fecha_desde, fecha_hasta, sociedad, tipo_doc):
    """Valida via OCR que los campos del formulario ZFIEC015 coincidan con lo esperado.

    Lee valores_bancos.json (generado por procesar_banco) y compara con lo
    detectado por leer_valores_zfiec015(). Lanza RuntimeError si hay diferencia,
    lo que dispara el retry en procesar_banco. Si el JSON no existe o el módulo
    OCR no está disponible, la validación se omite sin error.

    Args:
        proveedor (str): Número de proveedor esperado.
        fecha_desde (str): Fecha inicio esperada en formato DD.MM.YYYY.
        fecha_hasta (str): Fecha fin esperada en formato DD.MM.YYYY.
        sociedad (str): Código de sociedad esperado.
        tipo_doc (str): Tipo de documento esperado.

    Raises:
        RuntimeError: Si algún campo detectado por OCR no coincide con el esperado.
    """
    import json, pathlib
    _base = pathlib.Path(sys.executable).parent if getattr(sys, 'frozen', False) \
            else pathlib.Path(__file__).parent.parent
    ruta = _base / "valores_bancos.json"
    if not ruta.exists():
        _log.warning("Validación ZFIEC015 omitida — valores_bancos.json no existe")
        return
    with open(ruta, encoding="utf-8") as f:
        esperados = json.load(f)
    _log.info("Validando campos ZFIEC015 por OCR...")
    try:
        from transactions.validacion_Pantalla import leer_valores_zfiec015
    except (ImportError, SystemExit) as exc:
        _log.warning("Validación ZFIEC015 omitida — %s", exc)
        return
    detectados = leer_valores_zfiec015()
    # Solo comparar campos que el OCR de ZFIEC015 puede detectar
    # (valores_bancos.json contiene también campos de FB60 como "Texto Cabecera")
    diferencias = [
        f"{campo}: esperado={val_esp!r} detectado={detectados.get(campo)!r}"
        for campo, val_esp in esperados.items()
        if campo in detectados and detectados[campo] != val_esp
    ]
    if diferencias:
        msg = "Validación ZFIEC015 fallida:\n  " + "\n  ".join(diferencias)
        _log.error(msg)
        raise RuntimeError(msg)
    _log.info("Validación OCR ZFIEC015 OK. Valores detectados:")
    for k, v in detectados.items():
        _log.info("  OCR %-35s %s", k, repr(v) if v is not None else "N/D")
    print("  ✓ Validación ZFIEC015 OK")


def buscar(proveedor: str, fecha_desde: str, fecha_hasta: str,
           sociedad: str = None, tipo_doc: str = None) -> int:
    """Navega a ZFIEC015, llena el formulario y ejecuta la búsqueda (F8).

    Usa SAP Scripting si está disponible; si no, navega por teclado.
    Espera _SLEEP_CARGA segundos tras F8 para que cargue la grilla de resultados.

    Args:
        proveedor (str): Número de proveedor SAP (cuenta_mayor_sap del banco).
        fecha_desde (str): Fecha inicio en formato DD.MM.YYYY.
        fecha_hasta (str): Fecha fin en formato DD.MM.YYYY.
        sociedad (str, optional): Código de sociedad. Default: SAP_SOCIEDAD del .env.
        tipo_doc (str, optional): Tipo de documento. Default: TIPO_DOC_ZFIEC del .env.

    Returns:
        int:
            >0 → número de filas encontradas (via scripting).
            0  → sin resultados pendientes.
            -1 → hay datos pero no se pudo contar (sin SAP Scripting).

    Hardcoded:
        - _TITULO_ZFIEC = "Recepcion de documentos Electronicos"  (STRING)
        - _TIMEOUT_ZFIEC = 15                                     (TIMING — segundos)
        - _GRID_SAP_ID: ruta del control Grid en SAP              (STRING SAP)
        - "recepci", "electr": fragmentos para detectar título    (STRING)
    """
    sociedad = sociedad or os.getenv("SAP_SOCIEDAD", "")
    tipo_doc = tipo_doc or os.getenv("TIPO_DOC_ZFIEC", "")

    SAP.ir_a(os.getenv("TCODE_ZFIEC015", "ZFIEC015"))
    SAP.esperar_titulo(_TITULO_ZFIEC, timeout=_TIMEOUT_ZFIEC)
    SAP.verificar_pantalla(_TITULO_ZFIEC, "ZFIEC015-Formulario")
    time.sleep(_SLEEP_MEDIO)
    
    
    session = _get_session()
    if session and _CAMPOS_DISPONIBLES:
        _llenar_form_scripting(session, sociedad, proveedor, fecha_desde, fecha_hasta, tipo_doc)
    else:
        _llenar_form_teclado(sociedad, proveedor, fecha_desde, fecha_hasta, tipo_doc)

    SAP.activar()
    SAP.tab(1)
    time.sleep(_SLEEP_MEDIO)
    _validar_campos_zfiec(proveedor, fecha_desde, fecha_hasta, sociedad, tipo_doc)

    if session and _CAMPOS_DISPONIBLES:
        session.findById("wnd[0]").sendVKey(8)
        _log.debug("ZFIEC015 F8 enviado via scripting.")
    else:
        SAP.activar(_TITULO_ZFIEC_ES)
        SAP.f8()
        _log.debug("ZFIEC015 F8 enviado via teclado.")
    time.sleep(_SLEEP_CARGA)

    titulo = SAP.titulo_actual()
    _log.debug("ZFIEC015 post-F8: %r", titulo)

    if "recepci" not in titulo.lower() and "electr" not in titulo.lower():
        _log.info("Sin resultados en ZFIEC015 para proveedor %s", proveedor)
        return 0

    if session:
        try:
            grid = session.findById(_GRID_SAP_ID)
            filas = grid.RowCount
            _log.info("ZFIEC015: %d filas encontradas", filas)
            return filas
        except Exception as e:
            _log.debug("No se pudo leer RowCount via scripting: %s", e)

    return -1   # hay datos pero no se puede contar sin scripting


def _llenar_form_scripting(session, sociedad, proveedor,
                            fecha_desde, fecha_hasta, tipo_doc) -> None:
    """Llena el formulario ZFIEC015 usando SAP Scripting (más fiable que teclado).

    Escribe cada campo por ID SAP y prueba IDs alternativos si el principal falla.
    Selecciona el radio button "Pendiente" y envía F8.

    Args:
        session: Sesión SAP Scripting (objeto win32com).
        sociedad (str): Código de sociedad (ej. "2000").
        proveedor (str): Número de proveedor SAP.
        fecha_desde (str): Fecha inicio en formato DD.MM.YYYY.
        fecha_hasta (str): Fecha fin en formato DD.MM.YYYY.
        tipo_doc (str): Tipo de documento ZFIEC (ej. "01").

    Hardcoded:
        - sendVKey(8): código de F8 en SAP Scripting (NÚMERO MÁGICO SAP)
    """
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

    _log.debug("ZFIEC015 campos llenados via scripting.")


def _set_field(session, *ids, value: str) -> bool:
    """Escribe un valor en un campo SAP probando IDs alternativos en orden.

    Útil cuando el ID exacto del campo varía según versión/idioma de SAP.

    Args:
        session: Sesión SAP Scripting.
        *ids (str): Uno o más IDs SAP a probar en orden.
        value (str): Valor a escribir en el campo.

    Returns:
        bool: True si se escribió en algún ID, False si todos fallaron.
    """
    for fid in ids:
        try:
            session.findById(fid).text = value
            return True
        except Exception:
            continue
    _log.warning("No se encontró campo SAP para IDs: %s", ids)
    return False


def _llenar_form_teclado(sociedad, proveedor, fecha_desde, fecha_hasta, tipo_doc) -> None:
    """Llena el formulario ZFIEC015 navegando con teclado (fallback sin Scripting).

    Navega con Tab y Down hasta cada campo. Usa campo_ctrlA en Sociedad
    porque Home navega fuera del campo en ese formulario.

    Args:
        sociedad (str): Código de sociedad.
        proveedor (str): Número de proveedor SAP.
        fecha_desde (str): Fecha inicio DD.MM.YYYY.
        fecha_hasta (str): Fecha fin DD.MM.YYYY.
        tipo_doc (str): Tipo de documento.

    Hardcoded:
        - _TAB_PROVEEDOR, _TAB_FECHA_HASTA, _TAB_TIPO_DOC, _TAB_PENDIENTE: tabulaciones (CONFIG)
        - 3: número de Down para llegar a Fecha Desde (NÚMERO MÁGICO)
        - _TITULO_ZFIEC_ES: título con tilde para activar ventana (STRING)
    """
    SAP.activar(_TITULO_ZFIEC_ES)
    time.sleep(_SLEEP_CORTO)
    SAP.campo_ctrlA(sociedad)
    SAP.activar()
    time.sleep(_SLEEP_CORTO)

    SAP.tab(_TAB_PROVEEDOR)
    time.sleep(_SLEEP_CORTO)
    SAP.escribir(proveedor)
    time.sleep(_SLEEP_CORTO)

    SAP.activar()
    SAP.tecla('down')
    time.sleep(_SLEEP_CORTO)
    SAP.tecla('down')
    time.sleep(_SLEEP_CORTO)
    SAP.tecla('down')
    time.sleep(_SLEEP_CORTO)
    SAP.escribir(fecha_desde)
    time.sleep(_SLEEP_CORTO)

    SAP.tab(_TAB_FECHA_HASTA)
    time.sleep(_SLEEP_CORTO)
    SAP.escribir(fecha_hasta)
    time.sleep(_SLEEP_MEDIO)

    SAP.tab(_TAB_TIPO_DOC)
    time.sleep(_SLEEP_CORTO)
    SAP.escribir(tipo_doc)
    time.sleep(_SLEEP_CORTO)

    _log.debug("ZFIEC015 campos llenados via teclado.")


# ── PASO 2: Procesar filas de la grilla ──────────────────────


def procesar_documentos(banco: dict, max_docs: int = None, **_):
    """Abre FB60 desde la grilla e itera todos los documentos pendientes.

    Tras cada contabilización SAP regresa a ZFIEC015 con la grilla refrescada
    y el siguiente documento en row 0. El loop re-abre _abrir_fb60_teclado(0)
    hasta que no haya más docs o se alcance max_docs.

    Args:
        banco (dict): Configuración del banco.
        max_docs (int | None): Máximo de documentos a procesar. None = sin límite.
        **_: Absorbe kwargs no usados (fecha_desde, fecha_hasta).

    Returns:
        tuple[list, list]: (procesados, errores)
    """
    from transactions.fb60_kb import registrar_factura, ValidacionFB60Error
    procesados = []
    errores    = []

    if not _abrir_fb60_teclado(0):
        _log.info("Primer intento FB60 falló — reintentando en 2s...")
        time.sleep(_SLEEP_REINTENTAR)
        if not _abrir_fb60_teclado(0):
            pantalla = SAP.titulo_actual()
            if "recepci" in pantalla.lower():
                _log.info("Grilla vacía — sin documentos pendientes para este banco.")
                return procesados, errores
            _log.warning("FB60 no se abrió tras dos intentos. Pantalla: %r", pantalla)
            raise RuntimeError(
                f"FB60 no se abrió desde ZFIEC015 — pantalla actual: {pantalla!r}. "
                "Verifique la grilla manualmente."
            )

    n = 0
    while True:
        n += 1
        _log.info("--- LOOP doc_%d inicio ---", n)
        try:
            resultado = registrar_factura(banco)
            procesados.append(resultado)
            print(f"    ✓ Doc {n}: {resultado['sap_doc']}")
            _log.info("Procesado doc_%d → %s", n, resultado['sap_doc'])
        except ValidacionFB60Error as e:
            _log.warning("Validación FB60 fallida doc_%d — saltando al siguiente: %s", n, e)
            print(f"    ↺ doc_{n}: validación OCR fallida — siguiente documento")
            errores.append({"doc": f"doc_{n}", "error": str(e)})
            time.sleep(_SLEEP_CARGA)   # esperar que SAP regrese a grilla ZFIEC015
            if not _abrir_fb60_teclado(0):
                break
            continue
        except Exception as e:
            _log.error("Error doc_%d: %s", n, e, exc_info=True)
            print(f"    ✗ doc_{n}: {e}")
            errores.append({"doc": f"doc_{n}", "error": str(e)})
            break

        if max_docs and n >= max_docs:
            _log.info("Límite de %d docs alcanzado.", max_docs)
            break

        time.sleep(_SLEEP_MEDIO)
        titulo_post = SAP.titulo_actual()
        _log.info("doc_%d post-registro: título = %r", n, titulo_post)

        # Si FB60 sigue abierto (improbable en real), continuar directo
        if _TITULO_FB60.lower() in titulo_post.lower():
            continue

        # SAP regresó a ZFIEC015 — misma secuencia que primera fila
        if not _abrir_fb60_teclado(0):
            _log.info("Sin más documentos en grilla tras doc_%d.", n)
            break
        time.sleep(_SLEEP_ENTRE_DOCS)

    return procesados, errores

def _abrir_fb60_scripting(session, fila_idx: int) -> bool:
    """Abre FB60 para la fila indicada usando SAP Scripting.

    Re-obtiene el objeto Grid en cada llamada para evitar referencias
    desactualizadas tras interacciones previas con la grilla.

    Args:
        session: Sesión SAP Scripting activa.
        fila_idx (int): Índice 0-based de la fila a abrir en la grilla.

    Returns:
        bool: True si FB60 se abrió correctamente, False si falló.

    Hardcoded:
        - _GRID_SAP_ID: ruta del control Grid en SAP (STRING SAP)
        - _COL_FB60 = "FB60": nombre de columna del botón (STRING SAP)
        - "wnd[1]": nombre de ventana popup de confirmación (STRING SAP)
        - "wnd[1]/tbar[0]/btn[0]": ID botón OK del popup (STRING SAP)
        - _SLEEP_POPUP: espera apertura de FB60 (TIMING)
        - _SLEEP_MEDIO: espera tras confirmar popup (TIMING)
    """
    try:
        grid = session.findById(_GRID_SAP_ID)

        if fila_idx >= grid.RowCount:
            return False

        grid.setCurrentCell(fila_idx, _COL_FB60)
        grid.pressButton(fila_idx, _COL_FB60)

        time.sleep(_SLEEP_POPUP)

        try:
            if session.ActiveWindow.Name == "wnd[1]":
                session.findById("wnd[1]/tbar[0]/btn[0]").press()
                time.sleep(_SLEEP_MEDIO)
        except:
            pass

        return True

    except Exception as e:
        _log.warning("Fallo al interactuar con el Grid en fila %d: %s", fila_idx, e)
        return False


def _leer_barra_zfiec() -> str:
    """Lee la barra de estado de la ventana ZFIEC015 via pywinauto UIA.

    Mismo patrón que FB60: Footer → Edit[found_index=0] → window_text().
    Permite detectar advertencias o errores en ZFIEC015 en tiempo real.

    Returns:
        str: Texto de la barra de estado, o "" si no hay mensaje.
    """
    try:
        from pywinauto import Application
        app    = Application(backend="uia").connect(
            title_re=".*Recepci.*documentos.*", timeout=_TIMEOUT_CONNECT_BARRA
        )
        win    = app.window(title_re=".*Recepci.*documentos.*")
        footer = win.child_window(title="Footer", control_type="Pane")
        barra  = footer.child_window(control_type="Edit", found_index=0)
        return barra.window_text().strip()
    except Exception as e:
        _log.debug("No se pudo leer barra ZFIEC015: %s", e)
        return ""


def _esperar_y_confirmar_popup(timeout: float = _TIMEOUT_POPUP_CONFIRM) -> bool:
    """Espera el popup '¿Está seguro de ingresar la Factura?' y confirma con Sí.

    Estrategia:
    1. Detecta el pane 'FB60' (popup) dentro de la ventana ZFIEC015.
    2. Busca el botón 'Sí' como hijo del pane y hace click_input().
    3. Si el botón no es accesible via UIA (HTML puro), envía Enter como fallback.

    Args:
        timeout (float): Segundos máximos esperando que aparezca el popup.

    Returns:
        bool: True si el popup fue detectado y confirmado.
    """
    try:
        from pywinauto import Application
    except ImportError:
        _log.debug("pywinauto no disponible — Enter fallback")
        time.sleep(_SLEEP_POPUP)
        _kbd.press(_Key.enter); _kbd.release(_Key.enter)
        return True

    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            app  = Application(backend="uia").connect(
                title_re=".*Recepci.*documentos.*", timeout=_TIMEOUT_CONNECT
            )
            win  = app.window(title_re=".*Recepci.*documentos.*")
            pane = win.child_window(title="FB60", control_type="Pane")
            if not pane.exists(timeout=_TIMEOUT_PANE_EXISTS):
                time.sleep(_SLEEP_CORTO)
                continue

            _log.info("Popup confirmación FB60 detectado (%.1fs) — buscando botón Sí",
                      time.time() - t0)
            msg_barra = _leer_barra_zfiec()
            if msg_barra:
                _log.info("Barra ZFIEC015 durante popup: %r", msg_barra)

            # Intentar clic directo en botón "Sí" (puede ser Button, Hyperlink, etc.)
            for ctrl_type in ("Button", "Hyperlink", "ListItem"):
                try:
                    btn = pane.child_window(title="Sí", control_type=ctrl_type)
                    if btn.exists(timeout=_TIMEOUT_BTN_EXISTS):
                        btn.click_input()
                        _log.info("Clic en 'Sí' (%s) del popup (%.1fs)",
                                  ctrl_type, time.time() - t0)
                        time.sleep(_SLEEP_CORTO)
                        return True
                except Exception:
                    continue

            # Botón no accesible via UIA (HTML puro) — Enter funciona igual
            _log.debug("Botón 'Sí' no accesible via UIA — Enter fallback")
            _kbd.press(_Key.enter); _kbd.release(_Key.enter)
            return True

        except Exception:
            pass
        time.sleep(_SLEEP_CORTO)

    _log.warning("Popup confirmación no detectado en %.1fs — Enter fallback", timeout)
    _kbd.press(_Key.enter); _kbd.release(_Key.enter)
    return False


def _abrir_fb60_teclado(fila_idx: int, mismo_foco: bool = False) -> bool:
    """Abre FB60 navegando la grilla ZFIEC015 con teclado.

    Tres rutas según situación (NO mezclar):
      - mismo_foco=True: SAP acaba de regresar a ZFIEC015 con foco en el siguiente
                         doc. Solo Home → Right → Enter (sin F2 ni Down).
      - fila_idx == 0:   Grid recién cargado. F2 despierta el grid, luego navega.
      - fila_idx > 0:    Grid activo, avanzar fila con Down, luego navega.

    Returns:
        bool: True si aparece pantalla "Registrar factura" en _TIMEOUT_FB60 segundos.
    """
    SAP.activar(_TITULO_ZFIEC)
    time.sleep(_SLEEP_MEDIO)
    if fila_idx > 0 and not mismo_foco:
        # Grid activo, modo prueba: avanzar fila con Down
        _kbd.press(_Key.down); _kbd.release(_Key.down)
        time.sleep(_SLEEP_CORTO)
    else:
        # Primer ingreso o regreso de contabilizar: F2 activa foco del grid
        _kbd.press(_Key.f2); _kbd.release(_Key.f2)
        time.sleep(_SLEEP_LARGO)
    # Home → MIRO → Right → FB60 (común para los tres casos)
    _kbd.press(_Key.home); _kbd.release(_Key.home)
    time.sleep(_SLEEP_LARGO)
    _kbd.press(_Key.right); _kbd.release(_Key.right)
    time.sleep(_SLEEP_LARGO)
    _kbd.press(_Key.enter); _kbd.release(_Key.enter)   # abre popup de confirmación HTML
    # Reintentos: Enter cada 1.5s hasta que FB60 aparezca (máx 12s)
    for _intento in range(_MAX_INTENTOS_POPUP):
        time.sleep(_SLEEP_LARGO)
        if _TITULO_FB60.lower() in SAP.titulo_actual().lower():
            break
        SAP.enter()
        _log.info("Popup ZFIEC015: Enter intento %d", _intento + 1)
    try:
        SAP.esperar_titulo(_TITULO_FB60, timeout=_TIMEOUT_FB60)
        return True
    except RuntimeError:
        _log.warning("FB60 no se abrió en %ds. Pantalla: %r",
                     _TIMEOUT_FB60, SAP.titulo_actual())
        return False


def _cerrar_fb60_si_abierto() -> None:
    """Cierra FB60 si está abierto, para poder continuar con el siguiente banco.

    Detecta si la pantalla activa es FB60 por el título. Si lo está,
    envía F12 y confirma el popup de abandono con Enter.

    Hardcoded:
        - _TITULO_FB60, _TITULO_FB60_ALT: títulos de FB60 (STRING)
        - "ingresar factura": variante de título (STRING)
        - "tratamiento": popup de abandono SAP (STRING)
    """
    titulo = SAP.titulo_actual()
    if not any(p in titulo.lower() for p in (_TITULO_FB60.lower(), "ingresar factura")):
        return
    _log.debug("Cerrando FB60 residual antes de continuar.")
    SAP.activar()
    SAP.f12()
    time.sleep(_SLEEP_LARGO)
    titulo2 = SAP.titulo_actual().lower()
    if any(p in titulo2 for p in (_TITULO_FB60.lower(), "ingresar factura", "tratamiento")):
        SAP.enter()   # Sí tiene foco por defecto
        time.sleep(_SLEEP_LARGO)