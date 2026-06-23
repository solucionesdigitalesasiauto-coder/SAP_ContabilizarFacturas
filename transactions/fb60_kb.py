"""FB60 — Registro de factura de acreedor (navegación por teclado)."""
import os
import re
import time
import logging
import pyperclip
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


def _pegar(valor: str) -> None:
    """Pega un valor en el campo activo vía portapapeles + Ctrl+V.

    No usa Ctrl+A (seleccionaría filas en tabla) ni Enter (movería a fila siguiente).
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
    """Completa el formulario FB60 para una factura.

    Args:
        banco: dict con cuenta_mayor, centro_costo, texto_cabecera, texto_comision.

    Returns:
        Dict con sap_doc, fecha, cuenta_mayor, centro_costo.
    """
    cuenta_mayor = banco.get("cuenta_mayor", "")
    centro_costo = banco.get("centro_costo", "")
    via_pago     = os.getenv("VIA_PAGO", "")
    ind_impuesto = os.getenv("INDICADOR_IMPUESTO", "")
    texto_cab    = banco["texto_cabecera"]
    texto_com    = banco["texto_comision"]

    _log.debug("FB60: cuenta_mayor=%r  centro_costo=%r", cuenta_mayor, centro_costo)

    SAP.esperar_titulo("Registrar factura", timeout=8)
    SAP.verificar_pantalla("Registrar factura", "FB60-Inicio")
    SAP.posicionar_ventana()         # primero posicionar (puede redibujar SAP)
    SAP.activar("Registrar factura") # luego activar para restaurar el foco
    time.sleep(0.4)

    fecha_capturada = _copiar_fecha_factura()
    _llenar_fecha_contabilizacion(fecha_capturada)
    _marcar_calc_impuestos()
    _ingresar_indicador_impuesto(ind_impuesto)
    _posicion_normal(cuenta_mayor, texto_com, centro_costo)
    _salir_tabla_y_limpiar_advertencia()
    _llenar_pestana_pago(via_pago)
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
    SAP.activar()
    SAP.tab(_TAB_FECHA_FACTURA)
    SAP.copiar()
    time.sleep(0.4)
    return pyperclip.paste().strip()


def _llenar_fecha_contabilizacion(fecha: str) -> None:
    pyperclip.copy(fecha)
    SAP.activar()
    SAP.tab(_TAB_FECHA_CONTAB)
    SAP.pegar_fecha()
    time.sleep(0.5)


def _marcar_calc_impuestos() -> None:
    SAP.activar()
    SAP.tab(_TAB_CALC_IMP)
    SAP.tecla('space')
    time.sleep(0.4)


def _ingresar_indicador_impuesto(ind_impuesto: str) -> None:
    SAP.activar()
    if _TAB_IND_IMP > 0:
        SAP.tab(_TAB_IND_IMP)
    SAP.escribir(ind_impuesto)
    time.sleep(0.4)
    SAP.activar()
    SAP.tab(1)
    time.sleep(0.5)


def _posicion_normal(cuenta_mayor: str, texto_com: str, centro_costo: str) -> None:
    """Entradas subsecuentes — tabla ya inicializada, Ctrl+Shift+Tab normaliza antes de Tab(2)."""
    SAP.activar()
    SAP.tab(1)
    time.sleep(0.2)
    SAP.tecla('down')
    time.sleep(0.3)
    with _kbd.pressed(_Key.ctrl, _Key.shift):   # Ctrl+Shift+Tab: normaliza posición en tabla
        _kbd.press(_Key.tab); _kbd.release(_Key.tab)
    time.sleep(0.2)
    SAP.tab(2)                                  # → Cta.mayor
    time.sleep(0.5)
    _pegar(cuenta_mayor)
    time.sleep(0.3)
    _llenar_resto_posicion(texto_com, centro_costo)


def _llenar_resto_posicion(texto_com: str, centro_costo: str) -> None:
    """Campos comunes tras Cta.mayor: Importe, Texto, Centro Costo."""
    SAP.tab(_TAB_POS_IMPORTE)
    SAP.activar()
    _pegar("*")
    time.sleep(0.3)

    SAP.tab(_TAB_POS_TEXTO)
    SAP.activar()
    _pegar(texto_com)
    time.sleep(0.3)

    SAP.tab(_TAB_POS_CCOSTO)
    SAP.escribir(centro_costo)
    time.sleep(0.9)


def _salir_tabla_y_limpiar_advertencia() -> None:
    SAP.activar()
    SAP.salir_tabla()
    SAP.activar()
    SAP.enter()
    time.sleep(0.4)
    SAP.activar()
    SAP.enter()   # limpia advertencia "vencimiento en el pasado"
    time.sleep(0.4)


def _llenar_pestana_pago(via_pago: str) -> None:
    SAP.activar()
    SAP.siguiente_pestana()
    time.sleep(0.4)
    SAP.tecla('down')
    time.sleep(0.2)
    SAP.tecla('down')
    time.sleep(0.2)
    SAP.tecla('down')
    time.sleep(0.2)
    _pegar(via_pago)
    time.sleep(0.3)


def _llenar_pestana_detalle(texto_cab: str) -> None:
    SAP.activar()
    SAP.siguiente_pestana()
    time.sleep(0.5)
    SAP.activar()
    SAP.tab(1)
    SAP.activar()
    _pegar(texto_cab)
    time.sleep(0.3)


def _contabilizar_o_cancelar(fecha_capturada: str) -> str:
    SAP.activar()
    if os.getenv("CONTABILIZAR", "0") == "1":
        return _contabilizar(fecha_capturada)
    return _cancelar(fecha_capturada)


def _contabilizar(fecha_capturada: str) -> str:
    SAP.ctrl_s()
    time.sleep(0.8)
    # Confirmar cualquier popup que aparezca antes de que SAP vuelva a ZFIEC015:
    # puede ser el diálogo propio de FB60 ("registrar factura") o un popup
    # de confirmación externo con título diferente.
    if "recepci" not in SAP.titulo_actual().lower():
        SAP.enter()
        time.sleep(0.5)
    for _ in range(40):   # espera activa hasta 8 s
        time.sleep(0.2)
        if "registrar factura" not in SAP.titulo_actual().lower():
            break
    nro = _capturar_numero_doc()
    _log.info("FB60 contabilizado — Doc: %s  Fecha: %s", nro, fecha_capturada)
    return nro


def _cancelar(fecha_capturada: str) -> str:
    SAP.f12()
    time.sleep(0.8)
    titulo = SAP.titulo_actual().lower()
    # Sí tiene foco por defecto en el popup de abandono — Enter directo (sin Tab)
    if "registrar factura" in titulo or "ingresar factura" in titulo:
        SAP.enter()
        time.sleep(0.8)
    if "tratamiento" in SAP.titulo_actual().lower():
        SAP.enter()   # Enter = Sí en popup de abandono
        time.sleep(0.5)
    _log.info("FB60 cancelado (modo prueba) — Fecha: %s", fecha_capturada)
    return "PRUEBA"


def _capturar_numero_doc() -> str:
    """Lee el número de documento tras contabilizar.

    Tras Ctrl+S, SAP puede volver directamente a ZFIEC015 (sin popup)
    o mostrar un popup de confirmación. Solo presiona Enter si hay popup;
    si ya estamos en ZFIEC015 el Enter es espurio y desestabiliza el loop.
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
        time.sleep(0.5)

    return nro or "???"


def _extraer_numero(texto: str) -> str:
    """Extrae el número de documento de un texto SAP."""
    m = re.search(r"[Dd]oc[\.:\s]+(\w+)\s+se\s+contabiliz", texto)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{8,12})\b", texto)
    return m.group(1) if m else ""
