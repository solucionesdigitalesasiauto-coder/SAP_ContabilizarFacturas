"""
SAP - Automatización de Comisiones Bancarias
Empresa: ASIAUTO S.A.

Flujo:
  1. Si SAP no está abierto lo abre y hace login automáticamente
  2. Por cada banco seleccionado:
     a. ZFIEC015 → buscar documentos pendientes
     b. Por cada documento: FB60 → registrar y contabilizar
"""

import json
import logging
import os
import sys
import time
import calendar
import ctypes
import subprocess
import unicodedata
import win32api
import win32con
import win32gui
import win32process
from datetime import date
from dotenv import load_dotenv

# DPI awareness: pyautogui usa pixels físicos igual que Win32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Directorio del exe (frozen) o del script (desarrollo)
_BASE = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
        else os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(_BASE, ".env"))
load_dotenv(os.path.join(_BASE, "correos", ".env.privado"), override=True)

_LOG_PATH = os.path.join(_BASE, "sap_combancos.log")
try:
    logging.basicConfig(
        filename=_LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(module)s] %(message)s",
        encoding="utf-8",
        force=True,
    )
except OSError:
    import tempfile as _tmp
    _LOG_PATH = os.path.join(_tmp.gettempdir(), "sap_combancos.log")
    logging.basicConfig(
        filename=_LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(module)s] %(message)s",
        encoding="utf-8",
        force=True,
    )
_console = logging.StreamHandler()
_console.setLevel(logging.INFO)
_console.setFormatter(logging.Formatter("  [%(levelname)s] %(module)s: %(message)s"))
logging.getLogger().addHandler(_console)
_log = logging.getLogger("main")
print(f"  Log → {_LOG_PATH}", flush=True)

BANCOS_FILE = os.path.join(_BASE, "bancos.json")

MANDANTE = os.getenv("SAP_MANDANTE", "600")
USUARIO  = os.getenv("SAP_USUARIO",  "")
PASSWORD = os.getenv("SAP_PASSWORD", "")
SAPLOGON = r"C:\Program Files\SAP\FrontEnd\SAPGUI\saplogon.exe"


# ─────────────────────────────────────────────────────────────
# Abrir SAP y login automático
# ─────────────────────────────────────────────────────────────

def _sin_tildes(s: str) -> str:
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii").lower()


def _hwnd_con_titulo(texto: str):
    result = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and texto.lower() in win32gui.GetWindowText(hwnd).lower():
            result.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return result[0] if result else None


def _manejar_popup_sesion_multiple():
    import pyautogui
    hwnd = _hwnd_con_titulo("licencia")
    if not hwnd:
        return
    print("  Popup sesión múltiple — continuando sin cerrar otras sesiones...")
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.4)
    pyautogui.press('tab')
    time.sleep(0.2)
    pyautogui.press('up')
    time.sleep(0.2)
    pyautogui.press('enter')
    time.sleep(1.5)


def _hwnd_logon():
    result = []
    def cb(hwnd, _):
        if "SAP Logon" in win32gui.GetWindowText(hwnd) and win32gui.IsWindowVisible(hwnd):
            result.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return result[0] if result else None


def _traer_al_frente(hwnd):
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.3)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        tid_yo = win32api.GetCurrentThreadId()
        tid_el = win32process.GetWindowThreadProcessId(hwnd)[0]
        ctypes.windll.user32.AttachThreadInput(tid_yo, tid_el, True)
        win32gui.SetForegroundWindow(hwnd)
        ctypes.windll.user32.AttachThreadInput(tid_yo, tid_el, False)
    time.sleep(0.5)


def _control_lista_logon(hwnd_logon):
    candidatos = []
    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        r = win32gui.GetWindowRect(hwnd)
        w, h = r[2] - r[0], r[3] - r[1]
        if w > 150 and h > 80:
            candidatos.append((w * h, hwnd, r))
    win32gui.EnumChildWindows(hwnd_logon, cb, None)
    if not candidatos:
        return None, None
    candidatos.sort(reverse=True)
    _, hwnd, rect = candidatos[0]
    return hwnd, rect


def _cerrar_popup_error_sap() -> bool:
    """Detecta popup de error de conexión SAP (título 'SAP GUI for Windows').
    Lo cierra con Escape y retorna True si había un popup."""
    import pyautogui
    hwnd = _hwnd_con_titulo("SAP GUI for Windows")
    if not hwnd:
        return False
    texto = win32gui.GetWindowText(hwnd)
    print(f"  [!] Popup de error SAP detectado: {texto!r}")
    try:
        _traer_al_frente(hwnd)
        time.sleep(0.3)
        pyautogui.press('escape')
        time.sleep(0.5)
    except Exception:
        pass
    return True


def _sesion_activa() -> bool:
    import sap_gui as SAP
    return SAP._encontrar_hwnd() is not None


def _en_pantalla_login() -> bool:
    import sap_gui as SAP
    t = _sin_tildes(SAP.titulo_actual()).strip()
    return not t or t in ("sap",) or len(t) <= 4


def _mover_ventana_origen(hwnd):
    r = win32gui.GetWindowRect(hwnd)
    w, h = r[2] - r[0], r[3] - r[1]
    win32gui.MoveWindow(hwnd, 0, 0, w, h, True)
    time.sleep(0.2)


def abrir_sap_logon():
    hwnd = _hwnd_logon()
    if hwnd:
        print("  SAP Logon 64 ya está abierto.")
        _traer_al_frente(hwnd)
        _mover_ventana_origen(hwnd)
        return hwnd

    print("  Lanzando SAP Logon 64...", end="", flush=True)
    subprocess.Popen([SAPLOGON])
    for _ in range(20):
        time.sleep(1)
        print(".", end="", flush=True)
        hwnd = _hwnd_logon()
        if hwnd:
            break
    print()
    if not hwnd:
        raise RuntimeError("SAP Logon no respondió en 20 s.")
    time.sleep(1.0)
    _traer_al_frente(hwnd)
    _mover_ventana_origen(hwnd)
    print("  SAP Logon abierto.")
    return hwnd


def conectar_ps4(hwnd_logon):
    import pyautogui
    _traer_al_frente(hwnd_logon)
    _, rect_lista = _control_lista_logon(hwnd_logon)
    if rect_lista:
        cx = (rect_lista[0] + rect_lista[2]) // 2
        cy = rect_lista[1] + 80
        pyautogui.click(cx, cy)
        print(f"  Clic en lista SAP Logon ({cx},{cy})")
    else:
        r = win32gui.GetWindowRect(hwnd_logon)
        pyautogui.click((r[0]+r[2])//2, (r[1]+r[3])//2)
        print("  Clic en centro de ventana (fallback)")
    time.sleep(0.4)
    pyautogui.press("p")
    time.sleep(0.3)
    pyautogui.press("enter")
    print("  Enter → PS4 PRODUCCION (type-ahead)")


def esperar_sesion(timeout=25) -> bool:
    print("  Esperando pantalla de login SAP...", end="", flush=True)
    for _ in range(timeout):
        time.sleep(1)
        print(".", end="", flush=True)
        if _sesion_activa():
            print()
            return True
    print()
    return False


def llenar_credenciales():
    import sap_gui as SAP
    import pyautogui
    if not USUARIO or not PASSWORD:
        raise RuntimeError("SAP_USUARIO y SAP_PASSWORD no están en el .env")
    if win32api.GetKeyState(win32con.VK_CAPITAL) & 1:
        print("  [!] Caps Lock activado — desactivando antes de escribir credenciales...")
        ctypes.windll.user32.keybd_event(0x14, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x14, 0, 0x0002, 0)
        time.sleep(0.15)
        if win32api.GetKeyState(win32con.VK_CAPITAL) & 1:
            raise RuntimeError("No se pudo desactivar Caps Lock. Hazlo manualmente y reintenta.")
        print("  [✓] Caps Lock desactivado.")
    print("  Llenando credenciales...")
    SAP.activar()
    SAP.mover_a_origen()
    time.sleep(0.5)

    # Intentar via SAP Scripting (más fiable — no depende de posición del cursor)
    _llenado_por_scripting = False
    try:
        sap_auto = win32com.client.GetObject("SAPGUI")
        session  = sap_auto.GetScriptingEngine.Children(0).Children(0)
        # Mandante — puede estar pre-llenado, se sobreescribe por si acaso
        for fid in ("wnd[0]/usr/txtRSYST-MANDT",):
            try: session.findById(fid).text = MANDANTE
            except Exception: pass
        # Usuario — probar IDs alternativos según versión SAP
        _usuario_ok = False
        for fid in ("wnd[0]/usr/txtRSYST-BNAME", "wnd[0]/usr/txtBNAME",
                    "wnd[0]/usr/txtRSYST-BNAME2"):
            try:
                session.findById(fid).text = USUARIO
                _usuario_ok = True
                break
            except Exception:
                continue
        if not _usuario_ok:
            raise RuntimeError("No se encontró campo Usuario en scripting")
        # Contraseña
        session.findById("wnd[0]/usr/pwdRSYST-BCODE").text = PASSWORD
        session.findById("wnd[0]").sendVKey(0)  # Enter
        _llenado_por_scripting = True
        print("  Credenciales enviadas via scripting.")
    except Exception as _e:
        _log.debug(f"Scripting login no disponible ({_e}) — usando teclado.")

    if not _llenado_por_scripting:
        # Teclado: SAP posiciona el cursor en Usuarios (Mandante e Idioma ya están llenos)
        SAP.activar()
        SAP.campo_ctrlA(USUARIO)    # escribe en Usuarios (cursor ya está ahí)
        SAP.tab(1)
        SAP.campo_ctrlA(PASSWORD)   # Clv.acc.
        time.sleep(0.2)
        SAP.activar()
        SAP.enter()
        print("  Credenciales enviadas via teclado.")

    time.sleep(2.0)
    _manejar_popup_sesion_multiple()
    time.sleep(1.5)
    try:
        SAP.activar(); SAP.enter(); time.sleep(1.0)
    except Exception:
        pass


def hacer_login():
    import sap_gui as SAP
    if _sesion_activa() and not _en_pantalla_login():
        print("  Sesión SAP activa — navegando a Easy Access...")
        try:
            SAP.activar()
            SAP.ir_a("/n")
        except Exception:
            pass
        print("  Listo — continuando desde Easy Access.")
        return

    if not (_sesion_activa() and _en_pantalla_login()):
        # SAP no está abierto — abrir y conectar
        hwnd_logon = abrir_sap_logon()
        conectar_ps4(hwnd_logon)

        if not esperar_sesion(timeout=25):
            print("  Primer intento fallido — reintentando...")
            _traer_al_frente(hwnd_logon)
            _, rect_lista = _control_lista_logon(hwnd_logon)
            if rect_lista:
                import pyautogui
                cx = (rect_lista[0] + rect_lista[2]) // 2
                cy = rect_lista[1] + 80
                pyautogui.click(cx, cy)
                time.sleep(0.3)
                pyautogui.press("p")
                time.sleep(0.3)
                pyautogui.press("enter")
            if not esperar_sesion(timeout=20):
                raise RuntimeError("No se pudo conectar a PS4. Revisa SAP Logon.")
    else:
        print("  Pantalla de login detectada — SAP ya estaba abierto.")

    llenar_credenciales()

    # Verificar que el login fue exitoso
    print("  Verificando login...", end="", flush=True)
    for _ in range(10):
        time.sleep(1)
        print(".", end="", flush=True)
        if not _en_pantalla_login():
            break
    print()
    if _en_pantalla_login():
        raise RuntimeError(
            "Credenciales incorrectas — verifica SAP_USUARIO y SAP_PASSWORD en el .env"
        )
    _log.info("Login exitoso.")
    print("  Login exitoso.")


# ─────────────────────────────────────────────────────────────
# Bancos y fechas
# ─────────────────────────────────────────────────────────────

def cargar_bancos() -> list:
    with open(BANCOS_FILE, encoding="utf-8") as f:
        return json.load(f)["bancos"]


def fechas_mes_actual():
    hoy = date.today()
    primero = hoy.replace(day=1).strftime("%d.%m.%Y")
    ultimo_dia = calendar.monthrange(hoy.year, hoy.month)[1]
    ultimo = hoy.replace(day=ultimo_dia).strftime("%d.%m.%Y")
    return primero, ultimo




# ─────────────────────────────────────────────────────────────
# Procesamiento
# ─────────────────────────────────────────────────────────────

def procesar_banco(banco: dict, fecha_desde: str, fecha_hasta: str, max_docs: int = None):
    """Busca documentos pendientes en ZFIEC015 y procesa cada uno en FB60."""
    from transactions.zfiec015_kb import buscar, procesar_documentos

    print(f"\n{'─'*55}")
    print(f"  Banco: {banco['nombre']}  |  Proveedor: {banco['cuenta_mayor_sap']}")
    print(f"  Período: {fecha_desde} al {fecha_hasta}")
    print(f"{'─'*55}")

    count = buscar(banco["cuenta_mayor_sap"], fecha_desde, fecha_hasta)
    if count == 0:
        print("  Sin documentos pendientes.")
        return [], []

    return procesar_documentos(banco, fecha_desde=fecha_desde, fecha_hasta=fecha_hasta, max_docs=max_docs)


def _notificar(notif, banco: str, procesados: list, errores: list, registros: list) -> None:
    """Envía correo de resumen. Registra warning si falla, sin cortar el flujo."""
    if not notif:
        return
    try:
        notif.notify_resumen_banco(banco, registros)
    except Exception as e:
        _log.warning("Correo de resumen no enviado (%s): %s", banco, e)
        print(f"  [!] Correo no enviado ({banco}): {e}")


def _notificar_error(notif, banco: str, error: str) -> None:
    """Envía correo de error crítico. Registra warning si falla, sin cortar el flujo."""
    if not notif:
        return
    try:
        notif.notify_error_banco(banco, error)
    except Exception as e:
        _log.warning("Correo de error no enviado (%s): %s", banco, e)
        print(f"  [!] Correo de error no enviado ({banco}): {e}")


def imprimir_resumen(resultados: list):
    print(f"\n{'═'*55}")
    print("  RESUMEN FINAL")
    print(f"{'═'*55}")
    total_ok  = sum(len(r["procesados"]) for r in resultados)
    total_err = sum(len(r["errores"])    for r in resultados)
    for r in resultados:
        print(f"  {r['banco']:20s}  ✓ {len(r['procesados'])} ok   ✗ {len(r['errores'])} errores")
        for p in r["procesados"]:
            print(f"    ✓ Doc SAP: {p['sap_doc']}  ({p['doc']})")
        for e in r["errores"]:
            print(f"    ✗ {e['doc']}: {e['error']}")
    print(f"{'─'*55}")
    print(f"  Total: {total_ok} contabilizados, {total_err} con error")
    print(f"{'═'*55}\n")


def _build_registros(banco: dict, procesados: list, errores: list) -> list:
    cuenta_mayor = banco.get("cuenta_mayor", "")
    centro_costo = banco.get("centro_costo", "")
    return [
        {
            "numero_doc":   p["sap_doc"],
            "fecha":        p.get("fecha", ""),
            "importe":      p.get("importe", ""),
            "cuenta_mayor": p.get("cuenta_mayor", cuenta_mayor),
            "centro_costo": p.get("centro_costo", centro_costo),
            "estado":       "CONTABILIZADO",
            "detalle":      "",
        }
        for p in procesados
    ] + [
        {
            "numero_doc":   "",
            "fecha":        "",
            "importe":      "",
            "cuenta_mayor": cuenta_mayor,
            "centro_costo": centro_costo,
            "estado":       "ERROR",
            "detalle":      e["error"],
        }
        for e in errores
    ]


# ─────────────────────────────────────────────────────────────
# Validación de entorno
# ─────────────────────────────────────────────────────────────

_TCODES_MENU = ("SESSION_MANAGER", "S000", "")


def _validar_entorno():
    # Detectar popup de error de conexión SAP antes de cualquier otra acción
    if _cerrar_popup_error_sap():
        print("  [!] SAP tiene un error de conexión (WSAECONNRESET u otro).")
        print("      Cierra SAP, verifica la red y vuelve a ejecutar.")
        sys.exit(1)

    # Caps Lock antes de escribir credenciales
    if win32api.GetKeyState(win32con.VK_CAPITAL) & 1:
        print("  [!] Caps Lock activado — desactivando automáticamente...")
        ctypes.windll.user32.keybd_event(0x14, 0, 0, 0)       # VK_CAPITAL down
        ctypes.windll.user32.keybd_event(0x14, 0, 0x0002, 0)  # VK_CAPITAL up
        time.sleep(0.15)
        if win32api.GetKeyState(win32con.VK_CAPITAL) & 1:
            print("  [!] No se pudo desactivar Caps Lock. Hazlo manualmente y vuelve a ejecutar.")
            sys.exit(1)
        print("  [✓] Caps Lock desactivado.")
    else:
        print("  [✓] Caps Lock inactivo.")

    # Abrir SAP + login automático si es necesario
    hacer_login()

    # Confirmar que quedamos en el menú principal via SAP Scripting
    try:
        import win32com.client
        sap_auto = win32com.client.GetObject("SAPGUI")
        session  = sap_auto.GetScriptingEngine.Children(0).Children(0)
        tcode    = (session.info.transaction or "").strip().upper()
        if tcode not in _TCODES_MENU:
            print(f"\n  [!] SAP quedó en transacción '{tcode}' tras el login.")
            print("      Ciérrala manualmente y vuelve a ejecutar.")
            sys.exit(1)
        print(f"  [✓] SAP en menú principal (tcode='{tcode}').")
    except Exception:
        print("  [!] SAP Scripting no disponible — no se puede verificar la transacción activa.")


# ─────────────────────────────────────────────────────────────
# Menú de opciones
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║   SAP Automatización – Comisiones Bancarias          ║")
    print("║   ASIAUTO S.A.                                       ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()
    _validar_entorno()

    bancos = cargar_bancos()
    bancos_a_procesar = sorted(
        [b for b in bancos if b["cuenta_mayor_sap"]],
        key=lambda b: b.get("orden", 99)
    )
    if not bancos_a_procesar:
        print("  No hay bancos con proveedor configurado en bancos.json")
        sys.exit(1)

    start_from = os.getenv("START_FROM_BANCO", "").strip().upper()
    if start_from:
        idx = next((i for i, b in enumerate(bancos_a_procesar)
                    if b["nombre"].upper() == start_from), None)
        if idx is None:
            print(f"  [!] START_FROM_BANCO='{start_from}' no encontrado en bancos.json — se ignora.")
        else:
            bancos_a_procesar = bancos_a_procesar[idx:]
            print(f"  Iniciando desde: {start_from}")

    fecha_desde, fecha_hasta = fechas_mes_actual()

    print(f"\n  Bancos  : {', '.join(b['nombre'] for b in bancos_a_procesar)}")
    print(f"  Período : {fecha_desde} – {fecha_hasta}")
    print()

    _max = os.getenv("MAX_DOCS_BANCO", "1")
    max_docs = int(_max) if _max.isdigit() and int(_max) > 0 else None
    _modo = "REAL (contabiliza)" if os.getenv("CONTABILIZAR", "0") == "1" else "PRUEBA (sin guardar)"
    _label_docs = str(max_docs) if max_docs else "Todos"
    print(f"  Docs/banco : {_label_docs}  |  Modo: {_modo}")
    print()

    try:
        from correos.notificador_sap import NotificadorSAP
        notif = NotificadorSAP()
        print("  [✓] Notificaciones por correo habilitadas.")
    except Exception as e:
        _log.warning(f"Notificaciones deshabilitadas: {e}")
        print(f"  [!] Notificaciones deshabilitadas: {e}")
        notif = None

    resultados = []
    for banco in bancos_a_procesar:
        nombre = banco["nombre"]
        _log.info("=== Inicio %s %s-%s ===", nombre, fecha_desde, fecha_hasta)
        try:
            procesados, errores = procesar_banco(banco, fecha_desde, fecha_hasta, max_docs=max_docs)
            resultados.append({"banco": nombre, "procesados": procesados, "errores": errores})
            _log.info("%s: procesados=%d errores=%d", nombre, len(procesados), len(errores))
            _notificar(notif, nombre, procesados, errores,
                       _build_registros(banco, procesados, errores))
        except Exception as e:
            msg = str(e)
            _log.error("ERROR CRÍTICO %s: %s", nombre, msg, exc_info=True)
            print(f"\n  ✗ Error en {nombre}: {msg}")
            resultados.append({"banco": nombre, "procesados": [],
                                "errores": [{"doc": "—", "error": msg}]})
            _notificar_error(notif, nombre, msg)
            continue   # sigue con el siguiente banco

    imprimir_resumen(resultados)

    print("  Cerrando SAP...")
    import sap_gui as _SAP
    _SAP.cerrar_sap()
    print("  SAP cerrado.")

    _log.info("=== Fin del proceso ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as _ex:
        import traceback
        _log.error(f"ERROR NO CONTROLADO: {_ex}", exc_info=True)
        print(f"\n  ERROR: {_ex}")
        traceback.print_exc()
    finally:
        if getattr(sys, "frozen", False):
            input("\n  Presiona Enter para cerrar...")

