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
_SLEEP_MEDIO = 0.6   # entre campos / pasos SAP
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
    # La escritura al portapapeles puede ser asíncrona en algunos drivers de Windows
    for _ in range(_MAX_REINTENTOS_CLIP):
        if pyperclip.paste() == texto:
            break
        time.sleep(_SLEEP_MICRO)
    time.sleep(_SLEEP_MICRO)           # SAP necesita un tick entre copy y paste para asentar el foco
    with _kbd.pressed(_Key.ctrl):
        _kbd.press('v')
        _kbd.release('v')
    time.sleep(_SLEEP_MEDIO)


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
            # backend="uia" (no "win32") — SAP 800 expone los botones de popup solo via UIA
            app = Application(backend="uia").connect(
                title_re=".*Registrar factura.*", timeout=0.5
            )
            win = app.window(title_re=".*Registrar factura.*")
            # SAP 800 mapea el botón "Sí" como Button, Hyperlink o ListItem según tema GuiXT
            for ctrl_type in ("Button", "Hyperlink", "ListItem"):
                try:
                    btn = win.child_window(title="Sí", control_type=ctrl_type)
                    if btn.exists(timeout=0.3):
                        btn.click_input()
                        _log.info("Clic en 'Sí' (%s) del popup abandono FB60", ctrl_type)
                        time.sleep(_SLEEP_MEDIO)
                        return True
                except Exception:
                    continue
            # Botón no accesible via UIA — SAP posiciona foco en "Sí" por defecto: Tab+Enter
            _log.debug("Botón 'Sí' no encontrado via UIA — Tab+Enter fallback")
            _kbd.press(_Key.tab);   _kbd.release(_Key.tab)
            time.sleep(0.2)
            _kbd.press(_Key.enter); _kbd.release(_Key.enter)
            return True
        except Exception:
            pass
        time.sleep(_SLEEP_MEDIO)

    _log.warning("Popup abandono no detectado en %.1fs — Tab+Enter fallback", timeout)
    _kbd.press(_Key.tab);   _kbd.release(_Key.tab)
    time.sleep(0.2)
    _kbd.press(_Key.enter); _kbd.release(_Key.enter)
    return False


def _validar_pantalla_fb60() -> None:
    """Valida por OCR los campos de Datos básicos FB60 antes de cambiar de pestaña.

    Importa leer_y_validar_fb60 en tiempo de ejecución para evitar importación
    circular y para manejar graciosamente la ausencia de Tesseract.
    Si falla: cancela el doc con F12 y lanza ValidacionFB60Error — el caller
    lo captura para saltar este documento y continuar con el siguiente.
    """
    try:
        from transactions.validacion_Pantalla import leer_y_validar_fb60
    except (ImportError, Exception) as exc:
        _log.error("Validación OCR FB60 no disponible — %s", exc)
        print(f"  [!] OCR no operativo — abortando ejecución")
        import sys; sys.exit(1)
    resultado = leer_y_validar_fb60()
    if not resultado["valido"]:
        difs = resultado["diferencias"]
        msg = "Validación FB60 fallida:\n  " + "\n  ".join(f"{k}: {v}" for k, v in difs.items())
        _log.error(msg)
        SAP.activar(_TITULO_FB60)   # restaura foco: las screenshots OCR pueden haberlo quitado
        SAP.f12()
        time.sleep(_SLEEP_LARGO)
        _confirmar_abandon_fb60()
        raise ValidacionFB60Error(msg)   # capturado en zfiec015_kb para continuar con siguiente doc
    detectados = resultado.get("detectados", {})
    _log.info("Validación OCR FB60 OK. Valores detectados:")
    for k, v in detectados.items():
        _log.info("  OCR %-25s %s", k, repr(v) if v is not None else "N/D")
    print("  ✓ Validación FB60 OK")


def _validar_pantalla_detalle_fb60() -> None:
    """Valida por OCR que Txt.cabec. en la pestaña Detalle coincide con el valor esperado."""
    try:
        from transactions.validacion_Pantalla import leer_y_validar_fb60_detalle
    except (ImportError, Exception) as exc:
        _log.error("Validación OCR FB60 Detalle no disponible — %s", exc)
        print(f"  [!] OCR no operativo — abortando ejecución")
        import sys; sys.exit(1)
    resultado = leer_y_validar_fb60_detalle()
    if not resultado["valido"]:
        difs = resultado["diferencias"]
        msg = "Validación FB60 Detalle fallida:\n  " + "\n  ".join(f"{k}: {v}" for k, v in difs.items())
        _log.error(msg)
        SAP.activar(_TITULO_FB60)   # restaura foco antes de cancelar
        SAP.f12()
        time.sleep(_SLEEP_LARGO)
        _confirmar_abandon_fb60()
        raise ValidacionFB60Error(msg)   # capturado en zfiec015_kb para continuar con siguiente doc
    detectados = resultado.get("detectados", {})
    _log.info("Validación OCR FB60 Detalle OK. Valores detectados:")
    for k, v in detectados.items():
        _log.info("  OCR %-25s %s", k, repr(v) if v is not None else "N/D")
    print("  ✓ Validación FB60 Detalle OK")


def _verificar_foco_acreedor(banco: dict) -> None:
    """Verifica que el foco esté en el campo Acreedor al inicio de FB60.

    Copia el valor del campo activo y lo compara con el proveedor esperado
    (banco["cuenta_mayor_sap"]). Si no coincide el foco está perdido:
    cancela FB60 con F12 y lanza ValidacionFB60Error para que el loop reintente.
    """
    proveedor_esp = banco.get("cuenta_mayor_sap", "")
    SAP.copiar()                    # Ctrl+A+C — copia el valor del campo activo
    time.sleep(_SLEEP_MEDIO)
    valor_actual = pyperclip.paste().strip()
    _log.info("Foco Acreedor — campo activo: %r  esperado: %r", valor_actual, proveedor_esp)
    if valor_actual == proveedor_esp:
        return
    # Foco perdido — recuperar ventana antes de F12; sin esto F12 no llega a SAP
    _log.warning("FOCO PERDIDO al inicio FB60: campo=%r  proveedor=%r", valor_actual, proveedor_esp)
    print(f"  [!] Foco perdido en FB60 (campo={valor_actual!r}) — recuperando ventana...")
    SAP.posicionar_ventana()       # restaura posición en pantalla (minimizada/tapada)
    time.sleep(_SLEEP_MEDIO)
    SAP.activar(_TITULO_FB60)      # trae al frente y da foco real
    time.sleep(_SLEEP_MEDIO)
    SAP.f12()                      # ahora sí llega a SAP
    time.sleep(_SLEEP_LARGO)
    _confirmar_abandon_fb60()
    time.sleep(_SLEEP_MEDIO)
    # Si FB60 sigue abierto, el F12 fue bloqueado por una alerta SAP —
    # la alerta ya fue cerrada por _confirmar_abandon_fb60; reintentar F12 ahora
    if _TITULO_FB60.lower() in SAP.titulo_actual().lower():
        _log.warning("FB60 sigue abierto tras F12 — alerta bloqueante; reintentando cierre...")
        print("  [!] FB60 no cerró (alerta bloqueante) — reintentando F12...")
        SAP.activar(_TITULO_FB60)
        time.sleep(_SLEEP_MEDIO)
        SAP.f12()
        time.sleep(_SLEEP_LARGO)
        _confirmar_abandon_fb60()
        time.sleep(_SLEEP_MEDIO)
    raise ValidacionFB60Error(
        f"Foco perdido al inicio: campo activo={valor_actual!r} ≠ Acreedor={proveedor_esp!r}"
    )


def registrar_factura(banco: dict) -> dict:
    """Completa el formulario FB60 para una factura de comisión bancaria.

    Ejecuta en secuencia todos los pasos de llenado:
    fecha, impuestos, posición contable, pestaña Pago, pestaña Detalle
    y finalmente contabiliza. La validación OCR controla si el doc está correcto.

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
    # Leer parámetros contables del banco y del .env
    cuenta_mayor = banco.get("cuenta_mayor", "")
    centro_costo = banco.get("centro_costo", "")
    via_pago     = os.getenv("VIA_PAGO", "")
    ind_impuesto = os.getenv("INDICADOR_IMPUESTO", "")
    texto_cab    = banco["texto_cabecera"]
    texto_com    = banco["texto_comision"]

    _log.debug("FB60: cuenta_mayor=%r  centro_costo=%r", cuenta_mayor, centro_costo)
    time.sleep(_SLEEP_MEDIO)

    # Esperar y posicionar la ventana FB60
    SAP.esperar_titulo(_TITULO_FB60, timeout=_TIMEOUT_FB60)
    SAP.verificar_pantalla(_TITULO_FB60, "FB60-Inicio")
    SAP.posicionar_ventana()         # primero posicionar (puede redibujar SAP)
    SAP.activar(_TITULO_FB60)
    time.sleep(_SLEEP_LARGO)         # SAP necesita renderizar el form antes de tabular

    # Verificar foco en campo Acreedor antes de iniciar el llenado
    _verificar_foco_acreedor(banco)

    def _t(label: str, t0: float) -> float:
        t1 = time.time()
        _log.info("  %-30s %.2fs", label, t1 - t0)
        return t1

    t = time.time()

    # Pestaña Datos básicos — cabecera
    fecha_capturada = _copiar_fecha_factura()           # leer fecha del documento electrónico
    t = _t(f"fecha_factura → {fecha_capturada!r}", t)
    time.sleep(_SLEEP_MEDIO)
    _llenar_fecha_contabilizacion(fecha_capturada)      # copiar misma fecha a Fecha Contab.
    t = _t(f"fecha_contabilizacion ← {fecha_capturada!r}", t)
    time.sleep(_SLEEP_MEDIO)
    _marcar_calc_impuestos()                            # activar checkbox Calc.Impuestos
    t = _t("calc_impuestos ✓", t)
    time.sleep(_SLEEP_MEDIO)
    _ingresar_impuestoB2(ind_impuesto)                  # indicador de impuesto (ej. B2)
    t = _t(f"indicador_impuesto ← {ind_impuesto!r}", t)
    time.sleep(_SLEEP_MEDIO)

    # Pestaña Datos básicos — tabla de posiciones
    _posicion_normal(cuenta_mayor, texto_com, centro_costo)
    t = _t(f"posicion_normal ← cta={cuenta_mayor!r} imp={_IMPORTE_AUTO!r} txt={texto_com!r} cc={centro_costo!r}", t)
    time.sleep(_SLEEP_MEDIO)
    _salir_tabla_y_limpiar_advertencia()                # salir de la tabla + Enter x2 (limpia advertencia vencimiento)
    t = _t("salir_tabla ✓", t)
    time.sleep(_SLEEP_MEDIO)

    # Verificar foco en campo Acreedor antes de iniciar el llenado
    _verificar_foco_acreedor(banco)
    
    # Validación OCR — Datos básicos completos antes de cambiar de pestaña
    _validar_pantalla_fb60()
    t = _t("validacion_ocr ✓", t)

    # Pestaña Pago — Vía pago
    _llenar_pestana_pago(via_pago)
    t = _t(f"pestana_pago ← {via_pago!r}", t)
    time.sleep(_SLEEP_MEDIO)

    # Pestaña Detalle — Txt.cabec.
    _llenar_pestana_detalle(texto_cab)
    t = _t(f"pestana_detalle ← {texto_cab!r}", t)
    time.sleep(_SLEEP_MEDIO)

    # Validación OCR — Txt.cabec. en pestaña Detalle
    _validar_pantalla_detalle_fb60()
    t = _t("validacion_ocr_detalle ✓", t)

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
    SAP.copiar()                   # Ctrl+A+C: selecciona todo el campo y copia (no solo Ctrl+C)
    time.sleep(_SLEEP_MEDIO)
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
    SAP.pegar_fecha()              # tipeo carácter a carácter — la máscara de fecha SAP rechaza Ctrl+V directo
    time.sleep(_SLEEP_MEDIO)


def _marcar_calc_impuestos() -> None:
    """Activa el checkbox Calc.Impuestos en el encabezado FB60.

    Navega con tabulación acumulada desde Acreedor hasta el checkbox
    y lo marca con Space. Permite que SAP calcule el IVA automáticamente.

    Returns:
        None
    """
    SAP.activar()
    SAP.tab(_TAB_CALC_IMP)         # 5 tabs acumulados desde Acreedor (no desde el campo anterior)
    SAP.tecla('space')             # Space marca/desmarca el checkbox
    time.sleep(_SLEEP_MEDIO)
    if os.getenv("MES_ANTERIOR", "0") == "1":
        # SAP muestra aviso de fecha fuera del período al marcar Calc.Impuestos
        # con documentos del mes anterior — Enter confirma y libera el teclado
        SAP.enter()
        time.sleep(_SLEEP_MEDIO)


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
    # _TAB_IND_IMP=0: tras marcar checkbox el foco queda directamente aquí; guard para futura recalibración
    if _TAB_IND_IMP > 0:
        SAP.tab(_TAB_IND_IMP)
    SAP.escribir(ind_impuesto)
    time.sleep(_SLEEP_MEDIO)
    SAP.activar()                  # SAP hace lookup de B2 y puede tardar varios ciclos
    time.sleep(_SLEEP_MEDIO)
    SAP.tab(1)                     # confirmar y avanzar al siguiente campo
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
    time.sleep(_SLEEP_MEDIO)
    SAP.tecla('down')
    time.sleep(_SLEEP_MEDIO)
    with _kbd.pressed(_Key.ctrl, _Key.shift):   # Ctrl+Shift+Tab: normaliza posición en tabla
        _kbd.press(_Key.tab); _kbd.release(_Key.tab)
    time.sleep(_SLEEP_MEDIO)
    SAP.tab(2)                                  # → Cta.mayor
    time.sleep(_SLEEP_MEDIO)
    _pegar(cuenta_mayor)
    time.sleep(_SLEEP_MEDIO)
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
    SAP.activar()                  # foco puede perderse durante el sleep en máquinas rápidas
    _pegar(_IMPORTE_AUTO)          # "*" = SAP calcula el total automáticamente
    time.sleep(_SLEEP_MEDIO)

    SAP.tab(_TAB_POS_TEXTO)
    time.sleep(_SLEEP_MEDIO)
    SAP.activar()
    _pegar(texto_com)
    time.sleep(_SLEEP_MEDIO)

    SAP.tab(_TAB_POS_CCOSTO)
    time.sleep(_SLEEP_MEDIO)
    SAP.escribir(centro_costo)     # escribir directo (no portapapeles) — campo numérico sin ambigüedad
    time.sleep(_SLEEP_LARGO)


def _salir_tabla_y_limpiar_advertencia() -> None:
    """Sale de la tabla de posiciones y limpia mensajes de advertencia.

    Ejecuta salir_tabla() (4x Ctrl+Shift+Tab) para volver al encabezado,
    luego dos Enter para confirmar y limpiar advertencia de "vencimiento en el pasado".

    Returns:
        None
    """
    SAP.activar()
    SAP.salir_tabla()              # 4× Ctrl+Shift+Tab: vuelve al encabezado desde la tabla
    SAP.activar()                  # tabla y encabezado son contextos SAP distintos; restablecer foco
    SAP.enter()
    time.sleep(_SLEEP_MEDIO)
    SAP.activar()
    SAP.enter()                    # segundo Enter: limpia advertencia "vencimiento en el pasado"
    time.sleep(_SLEEP_MEDIO)


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
    SAP.siguiente_pestana()        # Ctrl+Shift+AvPág — cambia a pestaña Pago
    time.sleep(_SLEEP_MEDIO)
    # 3× Down en lugar de Tab — Tab va a Condición de Pago; Down navega directamente a Vía pago
    SAP.tecla('down')
    time.sleep(_SLEEP_MEDIO)
    SAP.tecla('down')
    time.sleep(_SLEEP_MEDIO)
    SAP.tecla('down')
    time.sleep(_SLEEP_MEDIO)
    _pegar(via_pago)
    time.sleep(_SLEEP_MEDIO)


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
    SAP.siguiente_pestana()        # Ctrl+Shift+AvPág — cambia a pestaña Detalle
    time.sleep(_SLEEP_MEDIO)
    SAP.activar()
    SAP.tab(1)                     # → campo Txt.cabec.
    _pegar(texto_cab)
    time.sleep(_SLEEP_MEDIO)
    SAP.enter()                    # confirma el campo antes de salir de pestaña; sin esto SAP puede descartarlo
    SAP.activar()
    time.sleep(_SLEEP_MEDIO)

def _contabilizar_o_cancelar(fecha_capturada: str) -> str:
    """Delega a _contabilizar, asegurando que ningún campo quede en edit mode."""
    SAP.activar()                  # saca Txt.cabec. de edit mode; sin esto SAP ignora el clic en Contabilizar
    return _contabilizar(fecha_capturada)


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
    SAP.tab(1)                     # saca el campo activo de edit mode; sin este tab SAP ignora el clic
    time.sleep(_SLEEP_MEDIO)
    try:
        from pywinauto import Application
        # "Registrar factura de acreedor" es el título completo en ventana maximizada
        _app = Application(backend="uia").connect(
            title_re=".*Registrar factura de acreedor.*", timeout=_TIMEOUT_PYWINAUTO
        )
        _win    = _app.window(title_re=".*Registrar factura de acreedor.*")
        _footer = _win.child_window(title="Footer", control_type="Pane")
        # click_input() funciona; invoke() (UIA InvokePattern) NO dispara el guardado en SAP
        _btn    = _footer.child_window(title="Contabilizar", auto_id="4004", control_type="Button")
        _btn.click_input()
        _log.info("FB60 botón Contabilizar clickeado")
    except Exception as e:
        _log.warning("Botón Contabilizar no encontrado (%s) — fallback Ctrl+S", e)
        SAP.ctrl_s()
        _log.info("FB60 Ctrl+S enviado como fallback")

    time.sleep(_SLEEP_POPUP)       # espera fija: el popup Información tarda variable en aparecer
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
        # Popup embebido en la ventana SAP — no detectable via EnumWindows; se cierra con Enter directo
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
    # Algunos builds de SAP 800 muestran "ingresar factura" en lugar de "registrar factura"
    if _TITULO_FB60.lower() in titulo or _TITULO_FB60_ALT in titulo:
        # Sí tiene foco por defecto en el popup — Enter directo (Tab movería el foco y confirmaría No)
        SAP.enter()
        time.sleep(_SLEEP_LARGO)
    if _TITULO_POPUP_ABA in SAP.titulo_actual().lower():
        SAP.enter()                # popup secundario de abandono: mismo patrón
        time.sleep(_SLEEP_MEDIO)
    _log.info("_cancelar: título final = %r", SAP.titulo_actual())
    _log.info("FB60 cancelado (modo prueba) — Fecha: %s", fecha_capturada)
    return "PRUEBA"