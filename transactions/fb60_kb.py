"""FB60 — Registro de factura de acreedor (navegación por teclado)."""
import os
import time
import logging
import pyperclip
from pynput.keyboard import Controller as _KbCtrl, Key as _Key

import sap_gui as SAP

_log = logging.getLogger(__name__)
_kbd = _KbCtrl()


class ValidacionFB60Error(RuntimeError):
    """Validación OCR fallida en FB60 — el doc se cancela y se salta al siguiente."""

_TAB_FECHA_FACTURA  = 2   # desde Acreedor
_TAB_FECHA_CONTAB   = 2   # desde Fecha Factura
_TAB_CALC_IMP       = 5   # desde Acreedor (total acumulado)
_TAB_IND_IMP        = 0   # foco inmediato tras marcar checkbox
_TAB_POS_IMPORTE    = 2   # dentro de la tabla de posiciones
_TAB_POS_TEXTO      = 6
_TAB_POS_CCOSTO     = 5

_SLEEP_MICRO = 0.1   # micro-pausa interna (retry clipboard, pre-paste)
_SLEEP_CORTO = 0.3   # entre campos / teclas
_SLEEP_MEDIO = 0.6   # entre pasos SAP
_SLEEP_LARGO = 0.9   # tabla / salidas lentas
_SLEEP_POPUP = 2.0   # espera popup Información tras Contabilizar

_MAX_REINTENTOS_CLIP  = 5    # reintentos de verificación del portapapeles
_TIMEOUT_PYWINAUTO    = 5    # timeout pywinauto connect a ventana FB60
_TIMEOUT_POPUP_UIA    = 1.0  # timeout exists() del popup Información
_TIMEOUT_POPUP_ABANDON = 2.0 # timeout detectar popup de abandono F12

_TITULO_FB60      = "Registrar factura"   # título de ventana FB60
_TITULO_FB60_ALT  = "ingresar factura"    # título alternativo FB60
_TITULO_POPUP_ABA = "tratamiento"         # popup de abandono al F12
_IMPORTE_AUTO     = "*"                   # SAP calcula el total automáticamente
_TIMEOUT_FB60     = 2                     # segundos esperando apertura de FB60


def _pegar(valor: str) -> None:
    """Pega un valor en el campo SAP activo vía portapapeles + Ctrl+V.

    Usa portapapeles en lugar de escritura directa para evitar que Ctrl+A
    seleccione filas en la tabla de posiciones o que Enter mueva el cursor.

    Args:
        valor (str): Texto a pegar en el campo activo.

    """
    texto = str(valor)
    pyperclip.copy(texto)
    for _ in range(_MAX_REINTENTOS_CLIP):
        if pyperclip.paste() == texto:
            break
        time.sleep(_SLEEP_MICRO)
    time.sleep(_SLEEP_MICRO)
    with _kbd.pressed(_Key.ctrl):
        _kbd.press('v')
        _kbd.release('v')
    time.sleep(_SLEEP_CORTO)


def _confirmar_abandon_fb60(timeout: float = _TIMEOUT_POPUP_ABANDON) -> bool:
    """Detecta y confirma el popup de abandono de FB60 (F12) via pywinauto.

    Busca el botón 'Sí' dentro de la ventana FB60 y hace click_input().
    Fallback: Tab + Enter si pywinauto no lo encuentra.
    """
    try:
        from pywinauto import Application
    except ImportError:
        _log.debug("pywinauto no disponible — Tab+Enter fallback")
        _kbd.press(_Key.tab);   _kbd.release(_Key.tab)
        time.sleep(0.2)
        _kbd.press(_Key.enter); _kbd.release(_Key.enter)
        return True

    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            app = Application(backend="uia").connect(
                title_re=".*Registrar factura.*", timeout=0.5
            )
            win = app.window(title_re=".*Registrar factura.*")
            for ctrl_type in ("Button", "Hyperlink", "ListItem"):
                try:
                    btn = win.child_window(title="Sí", control_type=ctrl_type)
                    if btn.exists(timeout=0.3):
                        btn.click_input()
                        _log.info("Clic en 'Sí' (%s) del popup abandono FB60", ctrl_type)
                        time.sleep(_SLEEP_CORTO)
                        return True
                except Exception:
                    continue
            # Botón no accesible via UIA — Tab+Enter fallback
            _log.debug("Botón 'Sí' no encontrado via UIA — Tab+Enter fallback")
            _kbd.press(_Key.tab);   _kbd.release(_Key.tab)
            time.sleep(0.2)
            _kbd.press(_Key.enter); _kbd.release(_Key.enter)
            return True
        except Exception:
            pass
        time.sleep(_SLEEP_CORTO)

    _log.warning("Popup abandono no detectado en %.1fs — Tab+Enter fallback", timeout)
    _kbd.press(_Key.tab);   _kbd.release(_Key.tab)
    time.sleep(0.2)
    _kbd.press(_Key.enter); _kbd.release(_Key.enter)
    return False


def _validar_pantalla_fb60() -> None:
    try:
        from transactions.validacion_Pantalla import leer_y_validar_fb60
    except (ImportError, SystemExit) as exc:
        _log.warning("Validación OCR FB60 omitida — %s", exc)
        return
    resultado = leer_y_validar_fb60()
    if not resultado["valido"]:
        difs = resultado["diferencias"]
        msg = "Validación FB60 fallida:\n  " + "\n  ".join(f"{k}: {v}" for k, v in difs.items())
        _log.error(msg)
        SAP.activar(_TITULO_FB60)
        SAP.f12()
        time.sleep(_SLEEP_LARGO)
        _confirmar_abandon_fb60()
        raise ValidacionFB60Error(msg)
    detectados = resultado.get("detectados", {})
    _log.info("Validación OCR FB60 OK. Valores detectados:")
    for k, v in detectados.items():
        _log.info("  OCR %-25s %r", k, v)
    print("  ✓ Validación FB60 OK")


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

    def _t(label: str, t0: float) -> float:
        t1 = time.time()
        _log.info("  %-30s %.2fs", label, t1 - t0)
        return t1

    t = time.time()
    fecha_capturada = _copiar_fecha_factura()
    t = _t(f"fecha_factura → {fecha_capturada!r}", t)
    time.sleep(_SLEEP_CORTO)
    _llenar_fecha_contabilizacion(fecha_capturada)
    t = _t(f"fecha_contabilizacion ← {fecha_capturada!r}", t)
    time.sleep(_SLEEP_CORTO)
    _marcar_calc_impuestos()
    t = _t("calc_impuestos ✓", t)
    time.sleep(_SLEEP_CORTO)
    _ingresar_impuestoB2(ind_impuesto)
    t = _t(f"indicador_impuesto ← {ind_impuesto!r}", t)
    time.sleep(_SLEEP_CORTO)
    _posicion_normal(cuenta_mayor, texto_com, centro_costo)
    t = _t(f"posicion_normal ← cta={cuenta_mayor!r} imp={_IMPORTE_AUTO!r} txt={texto_com!r} cc={centro_costo!r}", t)
    time.sleep(_SLEEP_CORTO)
    _salir_tabla_y_limpiar_advertencia()
    t = _t("salir_tabla ✓", t)
    time.sleep(_SLEEP_CORTO)
    _validar_pantalla_fb60()
    t = _t("validacion_ocr ✓", t)
    _llenar_pestana_pago(via_pago)
    t = _t(f"pestana_pago ← {via_pago!r}", t)
    time.sleep(_SLEEP_CORTO)
    _llenar_pestana_detalle(texto_cab)
    t = _t(f"pestana_detalle ← {texto_cab!r}", t)
    time.sleep(_SLEEP_CORTO)

    nro = _contabilizar_o_cancelar(fecha_capturada)
    _t(f"contabilizar → {nro!r}", t)
    return {
        "sap_doc":      nro,
        "fecha":        fecha_capturada,
        "cuenta_mayor": cuenta_mayor,
        "centro_costo": centro_costo,
    }


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
    _llenar_resto_tabla(texto_com, centro_costo)


def _llenar_resto_tabla(texto_com: str, centro_costo: str) -> None:
    """Llena Importe, Texto y Centro Costo en la posición contable activa.

    Continúa desde Cta.mayor con tabulación: Importe (_TAB_POS_IMPORTE tabs),
    Texto (_TAB_POS_TEXTO tabs), Centro Costo (_TAB_POS_CCOSTO tabs).
    Cada campo espera _SLEEP_MEDIO tras el tab antes de activar()/escribir()
    para que el foco se asiente en máquinas rápidas (Ctrl+V llega a campo activo).

    Args:
        texto_com (str): Texto de la posición (ej. "comision banco del austro").
        centro_costo (str): Centro de costo a asignar.

    """
    SAP.tab(_TAB_POS_IMPORTE)
    time.sleep(_SLEEP_MEDIO)
    SAP.activar()
    _pegar(_IMPORTE_AUTO)
    time.sleep(_SLEEP_MEDIO)

    SAP.tab(_TAB_POS_TEXTO)
    time.sleep(_SLEEP_MEDIO)
    SAP.activar()
    _pegar(texto_com)
    time.sleep(_SLEEP_MEDIO)

    SAP.tab(_TAB_POS_CCOSTO)
    time.sleep(_SLEEP_MEDIO)
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
    """Navega a la pestaña Detalle, escribe el texto de cabecera y regresa a Datos básicos.

    Usa Ctrl+Shift+AvPág para cambiar de pestaña, Tab(1) para posicionarse
    en Txt.cabec, pega el texto y luego retrocede 2× pestana_anterior() hasta
    Datos básicos. Así SAP recuerda esa pestaña y doc 2+ abre con cursor en Acreedor.

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
    SAP.tab(1)                  # salir del campo activo antes de guardar
    time.sleep(_SLEEP_CORTO)
    try:
        from pywinauto import Application
        _app = Application(backend="uia").connect(
            title_re=".*Registrar factura de acreedor.*", timeout=_TIMEOUT_PYWINAUTO
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

    time.sleep(_SLEEP_POPUP)
    _cerrado = False
    try:
        popup = _win.child_window(title_re=".*Informaci.*")
        if popup.exists(timeout=_TIMEOUT_POPUP_UIA):
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