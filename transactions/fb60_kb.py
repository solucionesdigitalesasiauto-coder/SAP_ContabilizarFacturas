"""FB60 — Registro de factura de acreedor (navegación por teclado)."""
import os
import re
import time
import logging
import pyperclip
import win32gui
import win32con
from pynput.keyboard import Controller as _KbCtrl, Key as _Key

import sap_gui as SAP

_log = logging.getLogger(__name__)
_kbd = _KbCtrl()

# ── Tab-counts calibrados con Au3Info (17-18/06/2026) ────────
_TAB_FECHA_FACTURA  = 2   # desde Acreedor
_TAB_FECHA_CONTAB   = 2   # desde Fecha Factura
_TAB_CALC_IMP       = 5   # desde Acreedor (total acumulado)
_TAB_IND_IMP        = 0   # foco inmediato tras marcar checkbox
_TAB_POS_IMPORTE    = 2   # dentro de la tabla de posiciones
_TAB_POS_TEXTO      = 6
_TAB_POS_CCOSTO     = 5

# ── Timings (ajustar si SAP responde más lento) ───────────────
_SLEEP_CORTO = 0.3   # pausa entre campos
_SLEEP_MEDIO = 0.5   # pausa entre pasos SAP
_SLEEP_LARGO = 0.9   # pausa en tabla / salidas lentas
_SLEEP_POPUP = 2.0   # espera popup Información tras Contabilizar

# ── Títulos y strings SAP ─────────────────────────────────────
_TITULO_FB60      = "Registrar factura"   # título de ventana FB60
_TITULO_FB60_ALT  = "ingresar factura"    # título alternativo FB60
_TITULO_POPUP_ABA = "tratamiento"         # popup de abandono al F12
_IMPORTE_AUTO     = "*"                   # SAP calcula el total automáticamente
_TIMEOUT_FB60     = 8                     # segundos esperando apertura de FB60


def _pegar(valor: str) -> None:
    """Pega un valor en el campo SAP activo vía portapapeles + Ctrl+V.

    Usa portapapeles en lugar de escritura directa para evitar que Ctrl+A
    seleccione filas en la tabla de posiciones o que Enter mueva el cursor.

    Args:
        valor (str): Texto a pegar en el campo activo.

    Returns:
        None

    Hardcoded:
        - 5: reintentos máximos de verificación del portapapeles (NÚMERO MÁGICO)
        - 0.05: sleep entre reintentos de portapapeles (TIMING)
        - 0.1: sleep antes de Ctrl+V (TIMING)
        - 0.25: sleep tras Ctrl+V (TIMING)
    """
    texto = str(valor)
    pyperclip.copy(texto)
    for _ in range(5):
        if pyperclip.paste() == texto:
            break
        time.sleep(0.05)
    time.sleep(0.1)
    with _kbd.pressed(_Key.ctrl):
        _kbd.press('v')
        _kbd.release('v')
    time.sleep(0.25)


def registrar_factura(banco: dict) -> dict:
    """Completa el formulario FB60 para una factura de comisión bancaria.

    Ejecuta en secuencia todos los pasos de llenado:
    fecha, impuestos, posición contable, pestaña Pago, pestaña Detalle
    y finalmente contabiliza o cancela según CONTABILIZAR en .env.

    Args:
        banco (dict): Configuración del banco con las claves:
            - cuenta_mayor (str): Cuenta mayor GL para la posición.
            - centro_costo (str): Centro de costo de la posición.
            - texto_cabecera (str): Texto del campo Txt.cabec en pestaña Detalle.
            - texto_comision (str): Texto descriptivo de la posición.

    Returns:
        dict: Resultado del registro con claves:
            - sap_doc (str): Número de documento SAP o "OK" / "PRUEBA".
            - fecha (str): Fecha de factura capturada del formulario.
            - cuenta_mayor (str): Cuenta mayor usada.
            - centro_costo (str): Centro de costo usado.

    Hardcoded:
        - _TITULO_FB60 = "Registrar factura"  (STRING — título esperado de pantalla)
        - _TIMEOUT_FB60 = 8                   (TIMING — segundos timeout esperar FB60)
        - VIA_PAGO: leído de os.getenv("VIA_PAGO")
        - INDICADOR_IMPUESTO: leído de os.getenv("INDICADOR_IMPUESTO")
    """
    cuenta_mayor = banco.get("cuenta_mayor", "")
    centro_costo = banco.get("centro_costo", "")
    via_pago     = os.getenv("VIA_PAGO", "")
    ind_impuesto = os.getenv("INDICADOR_IMPUESTO", "")
    texto_cab    = banco["texto_cabecera"]
    texto_com    = banco["texto_comision"]

    _log.debug("FB60: cuenta_mayor=%r  centro_costo=%r", cuenta_mayor, centro_costo)
    time.sleep(_SLEEP_CORTO)

    SAP.esperar_titulo(_TITULO_FB60, timeout=_TIMEOUT_FB60)
    SAP.verificar_pantalla(_TITULO_FB60, "FB60-Inicio")
    SAP.posicionar_ventana()         # primero posicionar (puede redibujar SAP)
    SAP.activar(_TITULO_FB60)
    time.sleep(_SLEEP_LARGO)         # SAP necesita renderizar el form antes de tabular

    fecha_capturada = _copiar_fecha_factura()
    time.sleep(_SLEEP_CORTO)
    _llenar_fecha_contabilizacion(fecha_capturada)
    time.sleep(_SLEEP_CORTO)
    _marcar_calc_impuestos()
    time.sleep(_SLEEP_CORTO)
    _ingresar_indicador_impuesto(ind_impuesto)
    time.sleep(_SLEEP_CORTO)
    _posicion_normal(cuenta_mayor, texto_com, centro_costo)
    time.sleep(_SLEEP_CORTO)
    _salir_tabla_y_limpiar_advertencia()
    time.sleep(_SLEEP_CORTO)
    _llenar_pestana_pago(via_pago)
    time.sleep(_SLEEP_CORTO)
    _llenar_pestana_detalle(texto_cab)

    nro = _contabilizar_o_cancelar(fecha_capturada)
    return {
        "sap_doc":      nro,
        "fecha":        fecha_capturada,
        "cuenta_mayor": cuenta_mayor,
        "centro_costo": centro_costo,
    }


# ── Pasos internos ────────────────────────────────────────────

def _copiar_fecha_factura() -> str:
    """Copia la Fecha Factura del encabezado FB60 al portapapeles.

    Navega desde el campo Acreedor con _TAB_FECHA_FACTURA tabs,
    aplica Ctrl+A+C para copiar el contenido del campo.

    Returns:
        str: Fecha de factura en formato SAP (DD.MM.YYYY) o cadena vacía.
    """
    SAP.activar()
    SAP.tab(_TAB_FECHA_FACTURA)
    SAP.copiar()
    time.sleep(_SLEEP_CORTO)
    return pyperclip.paste().strip()


def _llenar_fecha_contabilizacion(fecha: str) -> None:
    """Pega la fecha capturada en el campo Fecha Contabilización.

    Usa pegar_fecha() (tipeo carácter a carácter) porque SAP tiene una
    máscara que no acepta Ctrl+V directo en campos de fecha.

    Args:
        fecha (str): Fecha en formato DD.MM.YYYY, obtenida de _copiar_fecha_factura.

    Returns:
        None
    """
    pyperclip.copy(fecha)
    SAP.activar()
    SAP.tab(_TAB_FECHA_CONTAB)
    SAP.pegar_fecha()
    time.sleep(_SLEEP_MEDIO)


def _marcar_calc_impuestos() -> None:
    """Activa el checkbox Calc.Impuestos en el encabezado FB60.

    Navega con tabulación acumulada desde Acreedor hasta el checkbox
    y lo marca con Space. Permite que SAP calcule el IVA automáticamente.

    Returns:
        None
    """
    SAP.activar()
    SAP.tab(_TAB_CALC_IMP)
    SAP.tecla('space')
    time.sleep(_SLEEP_CORTO)


def _ingresar_impuestoB2(ind_impuesto: str) -> None:
    """Escribe el código de indicador de impuesto (ej. B2) en su campo.

    Tras marcar Calc.Impuestos, el foco queda inmediatamente en el campo
    Ind.Impuesto (_TAB_IND_IMP = 0). Escribe el código y avanza con Tab.

    Args:
        ind_impuesto (str): Código de indicador (ej. "B2" para IVA 15% Crédito).

    Returns:
        None
    """
    SAP.activar()
    time.sleep(_SLEEP_MEDIO)
    if _TAB_IND_IMP > 0:
        SAP.tab(_TAB_IND_IMP)
    SAP.escribir(ind_impuesto)
    time.sleep(_SLEEP_MEDIO)
    SAP.activar()
    time.sleep(_SLEEP_MEDIO)
    SAP.tab(1)
    time.sleep(_SLEEP_MEDIO)


def _posicion_normal(cuenta_mayor: str, texto_com: str, centro_costo: str) -> None:
    """Llena la fila de posición contable en la tabla de FB60.

    Secuencia: Tab(1) → Down → Ctrl+Shift+Tab (normaliza posición) → Tab(2) → Cta.mayor.
    Válido tanto para el primer como para ingresos subsecuentes (verificado producción 23/06/2026).

    Args:
        cuenta_mayor (str): Número de cuenta mayor GL (ej. "8110200002").
        texto_com (str): Texto descriptivo de la posición (ej. "comision banco del austro").
        centro_costo (str): Número de centro de costo (ej. "2047001103").

    Returns:
        None
    """
    SAP.activar()
    SAP.tab(1)
    time.sleep(_SLEEP_CORTO)
    SAP.tecla('down')
    time.sleep(_SLEEP_CORTO)
    with _kbd.pressed(_Key.ctrl, _Key.shift):   # Ctrl+Shift+Tab: normaliza posición en tabla
        _kbd.press(_Key.tab); _kbd.release(_Key.tab)
    time.sleep(_SLEEP_CORTO)
    SAP.tab(2)                                  # → Cta.mayor
    time.sleep(_SLEEP_MEDIO)
    _pegar(cuenta_mayor)
    time.sleep(_SLEEP_CORTO)
    _llenar_resto_posicion(texto_com, centro_costo)


def _llenar_resto_posicion(texto_com: str, centro_costo: str) -> None:
    """Llena Importe, Texto y Centro Costo en la posición contable activa.

    Continúa desde Cta.mayor con tabulación: Importe (_TAB_POS_IMPORTE tabs),
    Texto (_TAB_POS_TEXTO tabs), Centro Costo (_TAB_POS_CCOSTO tabs).

    Args:
        texto_com (str): Texto de la posición (ej. "comision banco del austro").
        centro_costo (str): Centro de costo a asignar.

    Returns:
        None

    Hardcoded:
        - _IMPORTE_AUTO = "*"  (STRING — indica a SAP que calcule el total)
    """
    SAP.tab(_TAB_POS_IMPORTE)
    SAP.activar()
    _pegar(_IMPORTE_AUTO)
    time.sleep(_SLEEP_CORTO)

    SAP.tab(_TAB_POS_TEXTO)
    SAP.activar()
    _pegar(texto_com)
    time.sleep(_SLEEP_CORTO)

    SAP.tab(_TAB_POS_CCOSTO)
    SAP.escribir(centro_costo)
    time.sleep(_SLEEP_LARGO)


def _salir_tabla_y_limpiar_advertencia() -> None:
    """Sale de la tabla de posiciones y limpia mensajes de advertencia.

    Ejecuta salir_tabla() (4x Ctrl+Shift+Tab) para volver al encabezado,
    luego dos Enter para confirmar y limpiar advertencia de "vencimiento en el pasado".

    Returns:
        None
    """
    SAP.activar()
    SAP.salir_tabla()
    SAP.activar()
    SAP.enter()
    time.sleep(_SLEEP_CORTO)
    SAP.activar()
    SAP.enter()   # limpia advertencia "vencimiento en el pasado"
    time.sleep(_SLEEP_CORTO)


def _llenar_pestana_pago(via_pago: str) -> None:
    """Navega a la pestaña Pago y escribe la vía de pago.

    Usa Ctrl+Shift+AvPág para cambiar de pestaña. La Vía pago está
    3 flechas Down desde el primer campo de la pestaña.

    Args:
        via_pago (str): Código de vía de pago (ej. "T" para transferencia).

    Returns:
        None
    """
    SAP.activar()
    SAP.siguiente_pestana()
    time.sleep(_SLEEP_CORTO)
    SAP.tecla('down')
    time.sleep(_SLEEP_CORTO)
    SAP.tecla('down')
    time.sleep(_SLEEP_CORTO)
    SAP.tecla('down')
    time.sleep(_SLEEP_CORTO)
    _pegar(via_pago)
    time.sleep(_SLEEP_CORTO)


def _llenar_pestana_detalle(texto_cab: str) -> None:
    """Navega a la pestaña Detalle y escribe el texto de cabecera.

    Usa Ctrl+Shift+AvPág para cambiar de pestaña, luego Tab(1) para
    posicionarse en el campo Txt.cabec y pega el texto.

    Args:
        texto_cab (str): Texto de cabecera del documento (ej. "BANCO DEL AUSTRO").

    Returns:
        None
    """
    SAP.activar()
    SAP.siguiente_pestana()
    time.sleep(_SLEEP_MEDIO)
    SAP.activar()
    SAP.tab(1)
    _pegar(texto_cab)
    time.sleep(_SLEEP_CORTO)
    SAP.enter()
    SAP.activar()
    time.sleep(_SLEEP_CORTO)


def _contabilizar_o_cancelar(fecha_capturada: str) -> str:
    """Despacha a modo real o prueba según variable de entorno CONTABILIZAR.

    Lee os.getenv("CONTABILIZAR", "0"): "1" → contabilizar, otro → cancelar.

    Args:
        fecha_capturada (str): Fecha de factura para incluir en el log.

    Returns:
        str: "OK" si se contabilizó, "PRUEBA" si se canceló.
    """
    SAP.activar()
    if os.getenv("CONTABILIZAR", "0") == "1":
        return _contabilizar(fecha_capturada)
    return _cancelar(fecha_capturada)


def _leer_barra_fb60() -> str:
    """Lee el mensaje de la barra de estado de FB60 via pywinauto UIA.

    Conecta a la ventana 'Registrar factura de acreedor' y lee el primer
    Edit del Footer (barra de estado de SAP donde aparece el resultado).

    Returns:
        str: Texto de la barra de estado, o "" si no se pudo leer.
    """
    try:
        from pywinauto import Application
        app = Application(backend="uia").connect(
            title_re=".*Registrar factura de acreedor.*", timeout=1
        )
        ventana = app.window(title_re=".*Registrar factura de acreedor.*")
        footer  = ventana.child_window(title="Footer", control_type="Pane")
        barra   = footer.child_window(control_type="Edit", found_index=0)
        return barra.window_text().strip()
    except Exception as e:
        _log.debug("No se pudo leer barra de estado FB60: %s", e)
        return ""


def _leer_popup_informacion() -> str:
    """Detecta el popup 'Información' que SAP muestra tras contabilizar.

    Es una ventana top-level (no hijo de FB60). Contiene el texto
    'Doc.XXXX se contabilizó en sociedad XXXX'. Lo cierra con Enter.

    Returns:
        str: Texto del popup si fue detectado y cerrado, "" si no existe.
    """
    hwnds = []
    def _enum(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and "Informaci" in win32gui.GetWindowText(hwnd):
            hwnds.append(hwnd)
    win32gui.EnumWindows(_enum, None)
    if not hwnds:
        return ""
    hwnd = hwnds[0]
    textos = []
    def _enum_hijos(h, _):
        t = win32gui.GetWindowText(h)
        if t.strip():
            textos.append(t.strip())
    win32gui.EnumChildWindows(hwnd, _enum_hijos, None)
    msg = " ".join(textos) or win32gui.GetWindowText(hwnd)
    # Solo es el popup correcto si contiene "contabiliz" — evita falsos positivos HTML
    if "contabiliz" not in msg.lower():
        return ""
    _log.info("Popup Información detectado: %r — cerrando con Enter", msg)
    win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
    time.sleep(0.1)
    win32gui.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_RETURN, 0)
    time.sleep(_SLEEP_CORTO)
    return msg


def _esperar_barra_estado(timeout: float) -> str:
    """Espera activa hasta que SAP confirme la contabilización.

    Sondea cada 0.3s buscando en orden:
    1. Popup 'Información' (ventana hija de FB60) — contiene el nro de doc.
    2. Barra de estado Footer Edit[0] de FB60.
    3. Título cambia a ZFIEC015 → contabilización completada.
    4. Otro título → diálogo inesperado → Enter para descartarlo.

    Returns:
        str: Texto con el nro de doc, "" si SAP salió de FB60 o timeout.
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        titulo = SAP.titulo_actual()
        if _TITULO_FB60.lower() in titulo.lower():
            msg = _leer_popup_informacion()
            if msg:
                return msg
            msg = _leer_barra_fb60()
            if msg:
                _log.info("Barra de estado FB60: %r", msg)
                return msg
        elif "Recepci" in titulo and "documentos" in titulo:
            _log.info("SAP regresó a ZFIEC015 — contabilización completada")
            return ""
        else:
            _log.info("Diálogo post-Ctrl+S: %r — Enter para confirmar", titulo)
            SAP.enter()
        time.sleep(_SLEEP_CORTO)
    _log.warning("Timeout %.0fs esperando confirmación FB60", timeout)
    return ""


def _contabilizar(fecha_capturada: str) -> str:
    """Guarda la factura, omite advertencias y captura el popup Información.

    Flujo:
    1. Clic en botón Contabilizar (auto_id=4004) — fallback Ctrl+S si falla.
    2. Enter 1s después para omitir advertencias amarillas (vencimiento, etc.).
    3. Polling win32gui buscando popup 'Información' con el nro de documento.
    4. Si no aparece en 30s, fallback a barra de estado.

    Args:
        fecha_capturada (str): Fecha de la factura, incluida en el log.

    Returns:
        str: Número de documento SAP, o "OK" si no se pudo extraer.
    """
    # ── PASO 1: guardar ───────────────────────────────────────────
    SAP.tab(1)                  # salir del campo activo antes de guardar
    time.sleep(_SLEEP_CORTO)
    try:
        from pywinauto import Application
        _app = Application(backend="uia").connect(
            title_re=".*Registrar factura de acreedor.*", timeout=5
        )
        _win    = _app.window(title_re=".*Registrar factura de acreedor.*")
        _footer = _win.child_window(title="Footer", control_type="Pane")
        _btn    = _footer.child_window(title="Contabilizar", auto_id="4004", control_type="Button")
        _btn.click_input()
        _log.info("FB60 botón Contabilizar clickeado")
    except Exception as e:
        _log.warning("Botón Contabilizar no encontrado (%s) — fallback Ctrl+S", e)
        SAP.ctrl_s()
        _log.info("FB60 Ctrl+S enviado como fallback")

    # ── PASO 2: cerrar popup Información (embebido en FB60) ──────────
    # Intenta via pywinauto (child sin filtro de control_type), fallback Enters
    time.sleep(_SLEEP_POPUP)
    _cerrado = False
    try:
        popup = _win.child_window(title_re=".*Informaci.*")
        if popup.exists(timeout=1.0):
            _log.info("Popup Información encontrado en UIA — cerrando")
            popup.type_keys("{ENTER}")
            _cerrado = True
    except Exception:
        pass
    if not _cerrado:
        _log.info("Popup no encontrado via UIA — enviando Enters de cierre")
        for _ in range(3):
            SAP.enter()
            time.sleep(_SLEEP_MEDIO)

    time.sleep(_SLEEP_MEDIO)
    _log.info("FB60 contabilizado — Doc: OK  Fecha: %s", fecha_capturada)
    return "OK"


def _cancelar(fecha_capturada: str) -> str:
    """Abandona el formulario FB60 sin guardar (modo prueba).

    Envía F12 para salir, luego Enter en el popup de abandono
    (SAP posiciona foco en Sí por defecto — NO usar Tab antes).

    Args:
        fecha_capturada (str): Fecha de la factura, incluida en el log.

    Returns:
        str: "PRUEBA"

    Hardcoded:
        - _TITULO_FB60    = "Registrar factura"  (STRING — título ventana FB60)
        - _TITULO_FB60_ALT = "ingresar factura"  (STRING — título alternativo)
        - _TITULO_POPUP_ABA = "tratamiento"      (STRING — popup de abandono SAP)
    """
    SAP.f12()
    time.sleep(_SLEEP_LARGO)
    titulo = SAP.titulo_actual().lower()
    _log.info("_cancelar: título tras F12 = %r", titulo)
    # Sí tiene foco por defecto en el popup de abandono — Enter directo (sin Tab)
    if _TITULO_FB60.lower() in titulo or _TITULO_FB60_ALT in titulo:
        SAP.enter()
        time.sleep(_SLEEP_LARGO)
    if _TITULO_POPUP_ABA in SAP.titulo_actual().lower():
        SAP.enter()
        time.sleep(_SLEEP_MEDIO)
    _log.info("_cancelar: título final = %r", SAP.titulo_actual())
    _log.info("FB60 cancelado (modo prueba) — Fecha: %s", fecha_capturada)
    return "PRUEBA"


def _capturar_numero_doc() -> str:
    """Lee el número de documento tras contabilizar.

    Tras Ctrl+S, SAP puede volver directamente a ZFIEC015 (sin popup)
    o mostrar un popup de confirmación. Solo presiona Enter si hay popup;
    si ya estamos en ZFIEC015 el Enter es espurio y desestabiliza el loop.

    Returns:
        str: Número de documento SAP o "???" si no se pudo capturar.

    Hardcoded:
        - 5: reintentos lectura portapapeles (NÚMERO MÁGICO)
        - "recepci": fragmento de título ZFIEC015 para detectar pantalla (STRING)
    """
    time.sleep(0.2)
    titulo = SAP.titulo_actual()

    pyperclip.copy("")
    with _kbd.pressed(_Key.ctrl):
        _kbd.press('c')
        _kbd.release('c')
    for _ in range(5):
        if pyperclip.paste():
            break
        time.sleep(0.05)
    cuerpo = pyperclip.paste().strip()

    nro = _extraer_numero(cuerpo) or _extraer_numero(titulo)

    # Cerrar popup solo si SAP NO volvió ya a la grilla ZFIEC015
    if "recepci" not in titulo.lower():
        SAP.enter()
        time.sleep(_SLEEP_MEDIO)

    return nro or "???"


def _extraer_numero(texto: str) -> str:
    """Extrae el número de documento SAP de un texto de popup o título.

    Busca el patrón "Doc. XXXX se contabilizó" o un número de 8-12 dígitos.

    Args:
        texto (str): Texto del popup o título de ventana SAP.

    Returns:
        str: Número de documento encontrado, o cadena vacía si no se halló.

    Hardcoded:
        - r"[Dd]oc[\.:\s]+(\w+)\s+se\s+contabiliz": patrón regex SAP (REGEX)
        - r"\b(\d{8,12})\b": patrón numérico 8-12 dígitos (REGEX)
    """
    m = re.search(r"[Dd]oc[\.:\s]+(\w+)\s+se\s+contabiliz", texto)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{8,12})\b", texto)
    return m.group(1) if m else ""
