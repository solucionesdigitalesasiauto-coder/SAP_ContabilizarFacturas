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

def _setup_logging() -> str:
    """Configura el sistema de logging con handler de archivo y retorna la ruta usada.

    Prueba rutas candidatas en orden hasta encontrar una con permisos de escritura.
    Si todas fallan retorna cadena vacía (el proceso sigue sin log de archivo).

    Returns:
        str: Ruta del archivo de log creado, o "" si ninguna candidata fue accesible.

    Hardcoded:
        - "sap_combancos.log": nombre del archivo de log (STRING)
        - "ASIAUTO/ComBancos": subdirectorio en APPDATA (STRING)
    """
    fmt = "%(asctime)s [%(levelname)s] [%(module)s] %(message)s"
    candidatos = [
        os.path.join(_BASE, "sap_combancos.log"),
        os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                     "ASIAUTO", "ComBancos", "sap_combancos.log"),
        os.path.join(os.path.expanduser("~"), "Desktop", "sap_combancos.log"),
        os.path.join(os.path.expanduser("~"), "sap_combancos.log"),
    ]
    for path in candidatos:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter(fmt))
            logging.getLogger().setLevel(logging.INFO)
            logging.getLogger().addHandler(handler)
            return path
        except Exception:
            continue
    return ""

_LOG_PATH = _setup_logging()
_console = logging.StreamHandler()
_console.setLevel(logging.INFO)
_console.setFormatter(logging.Formatter("  [%(levelname)s] %(module)s: %(message)s"))
logging.getLogger().addHandler(_console)
_log = logging.getLogger("main")
print(f"  Log → {_LOG_PATH}", flush=True)

# ── Configuración global ──────────────────────────────────────
BANCOS_FILE = os.path.join(_BASE, "bancos.json")
MANDANTE    = os.getenv("SAP_MANDANTE", "600")
USUARIO     = os.getenv("SAP_USUARIO",  "")
PASSWORD    = os.getenv("SAP_PASSWORD", "")
SAPLOGON    = r"C:\Program Files\SAP\FrontEnd\SAPGUI\saplogon.exe"

# ── Timeouts y offsets (ajustar según velocidad del sistema) ──
_TIMEOUT_LOGON_EXE   = 20    # segundos esperando que abra SAP Logon
_TIMEOUT_SESION      = 25    # segundos esperando pantalla de login SAP
_TIMEOUT_SESION_R2   = 20    # segundos en segundo intento de conexión
_TIMEOUT_LOGIN_VERIFY = 10   # reintentos de verificación post-login
_CLICK_OFFSET_Y_LOGON = 80   # offset Y en la lista SAP Logon para clic

# ── Constantes de detección ───────────────────────────────────
_TITULO_POPUP_LICENCIA = "licencia"   # fragmento del popup de sesión múltiple
_TITULO_POPUP_SAP_ERR  = "SAP GUI for Windows"  # popup de error de conexión
_TCODE_SAPLOGON_TEXT   = "SAP Logon"  # texto en título de SAP Logon
_TCODES_MENU           = ("SESSION_MANAGER", "S000", "")  # tcodes del menú principal

# ── Popup sesión múltiple — pasos Ctrl+Shift+Down (Flujo 1) ──
# POPUP_SESION_PASOS: primer intento. El reintento usa el valor alterno (5↔6).
_POPUP_SESION_PASOS       = int(os.getenv("POPUP_SESION_PASOS", "5"))
_POPUP_SESION_PASOS_RETRY = 6 if _POPUP_SESION_PASOS == 5 else 5

# ── Correo por lotes — enviar cada N registros en vez de esperar a que
# termine todo el banco. 0 o vacío = deshabilitado (un solo correo al final,
# comportamiento anterior).
_EMAIL_BATCH_SIZE = int(os.getenv("EMAIL_BATCH_SIZE", "5"))

# ── Timing (ajustar si el sistema es más lento) ──────────────
_SLEEP_MICRO  = 0.1   # micro-pausa (caps lock, pre-state check)
_SLEEP_CORTO  = 0.2   # entre pasos rápidos / pyautogui
_SLEEP_MEDIO  = 0.3   # entre pasos SAP (click, type-ahead)
_SLEEP_LARGO  = 0.5   # tras activar ventana / escape popup
_SLEEP_POLL   = 1.0   # intervalo de sondeo en loops
_SLEEP_SAP    = 1.5   # tras popup SAP / sesión múltiple
_SLEEP_INICIO = 2.0   # espera SAP arranque / cierre sesión
_MAX_INTENTOS_BANCO  = 3    # total intentos por banco (1 original + 2 reintentos)
_SLEEP_REINTENTO_BANCO = 5.0  # pausa antes de reintentar banco


# ─────────────────────────────────────────────────────────────
# Abrir SAP y login automático
# ─────────────────────────────────────────────────────────────

def _sin_tildes(s: str) -> str:
    """Normaliza un string eliminando tildes/diacríticos en minúsculas.

    Args:
        s (str): Texto a normalizar.

    Returns:
        str: Texto normalizado sin tildes, en minúsculas.
    """
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii").lower()


def _hwnd_con_titulo(texto: str):
    """Busca el HWND de la primera ventana visible cuyo título contiene el texto.

    Args:
        texto (str): Fragmento a buscar en el título (case-insensitive).

    Returns:
        int | None: HWND de la ventana, o None si no se encontró.
    """
    result = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and texto.lower() in win32gui.GetWindowText(hwnd).lower():
            result.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return result[0] if result else None



def _manejar_popup_sesion_multiple(pasos: int = 6):
    """Detecta y gestiona el popup de sesión múltiple de SAP.

    Selecciona "Continuar sin finalizar entradas existentes" (opción 2).
    Detecta posición del foco para elegir el flujo de navegación:
      - Flujo 1: foco en texto superior "El usuario..." → Ctrl+Shift+Down ×`pasos` + Enter
      - Flujo 2: foco en opción 3 "Cancelar" (default)  → Up×1                    + Enter

    Args:
        pasos (int): Repeticiones de Ctrl+Shift+Down en Flujo 1. Por defecto 6;
            si un intento previo con 6 terminó cayendo en "Cancelar" (SAP cambió
            el layout del popup), reintentar con 5.
    """
    from pynput.keyboard import Controller as _KbdCtrl, Key as _K
    hwnd = _hwnd_con_titulo(_TITULO_POPUP_LICENCIA)
    if not hwnd:
        return
    print("  Popup sesión múltiple — continuando sin cerrar otras sesiones...")

    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    tid_yo = win32api.GetCurrentThreadId()
    tid_el = win32process.GetWindowThreadProcessId(hwnd)[0]
    ctypes.windll.user32.AttachThreadInput(tid_yo, tid_el, True)
    win32gui.SetForegroundWindow(hwnd)
    ctypes.windll.user32.SetFocus(hwnd)
    time.sleep(_SLEEP_SAP)
    ctypes.windll.user32.AttachThreadInput(tid_yo, tid_el, False)

    # Detectar posición del foco por tipo de control (no por nombre — textos SAP lo dejan vacío)
    tipo_control = ""
    nombre_foco  = ""
    try:
        from pywinauto import Desktop
        foco = Desktop(backend="uia").get_focus()
        tipo_control = (foco.element_info.control_type or "").lower()
        nombre_foco  = (foco.element_info.name        or "").lower()
        _log.debug("Popup licencia — tipo=%r nombre=%r", tipo_control, nombre_foco)
    except Exception as _e:
        _log.debug("No se pudo leer foco: %s", _e)

    _kbd = _KbdCtrl()
    # Flujo 2: foco en radio button (opción 3 Cancelar) → Up×1 llega a opción 2
    # Flujo 1: foco en texto/label (nombre vacío) → Ctrl+Shift+Down×6 baja hasta opción 2
    if tipo_control == "radiobutton":
        print(f"  Flujo 2 — Up×1 (tipo={tipo_control!r} foco={nombre_foco!r})")
        _kbd.press(_K.up); _kbd.release(_K.up)
    else:
        print(f"  Flujo 1 — Ctrl+Shift+Down×{pasos} + Space (tipo={tipo_control!r} foco={nombre_foco!r})")
        for _ in range(pasos):
            with _kbd.pressed(_K.ctrl, _K.shift):
                _kbd.press(_K.down); _kbd.release(_K.down)
            time.sleep(_SLEEP_MICRO)
        time.sleep(_SLEEP_CORTO)
        _kbd.press(_K.space); _kbd.release(_K.space)  # activa el radio button

        # Verificar que el radio activado NO sea "Cancelar" antes de confirmar con
        # Enter — confirmado en producción 15/07/2026: un conteo de pasos
        # desalineado puede aterrizar en "Cancelar", y Enter ahí CIERRA SAP por
        # completo (la ventana desaparece y el siguiente activar() revienta con
        # "Ventana SAP no encontrada"). Si se detecta, Escape en vez de Enter —
        # deja el popup sin resolver, que es el estado que el caller ya reintenta
        # con el paso alterno (5↔6), en vez del crash irreversible.
        try:
            from pywinauto import Desktop as _Desktop
            foco2   = _Desktop(backend="uia").get_focus()
            nombre2 = (foco2.element_info.name or "").lower()
            if "cancel" in nombre2:
                _log.warning("Flujo 1 (pasos=%d) aterrizó en 'Cancelar' — Escape en vez de Enter", pasos)
                print(f"  [!] Flujo 1 aterrizó en 'Cancelar' (pasos={pasos}) — Escape en vez de Enter")
                _kbd.press(_K.esc); _kbd.release(_K.esc)
                time.sleep(_SLEEP_SAP)
                return
        except Exception as _e:
            _log.debug("No se pudo verificar radio seleccionado antes de Enter: %s", _e)

    time.sleep(_SLEEP_LARGO)
    _kbd.press(_K.enter); _kbd.release(_K.enter)
    time.sleep(_SLEEP_SAP)


def _hwnd_logon():
    """Busca el HWND de la ventana SAP Logon si está abierta y visible.

    Returns:
        int | None: HWND de SAP Logon, o None si no está abierta.

    Hardcoded:
        - _TCODE_SAPLOGON_TEXT = "SAP Logon": texto en el título (STRING)
    """
    result = []
    def cb(hwnd, _):
        if _TCODE_SAPLOGON_TEXT in win32gui.GetWindowText(hwnd) and win32gui.IsWindowVisible(hwnd):
            result.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return result[0] if result else None


def _traer_al_frente(hwnd):
    """Restaura y trae al frente una ventana Windows.

    Si SetForegroundWindow falla (restricción inter-proceso), usa AttachThreadInput
    para adjuntar el hilo actual al hilo de la ventana objetivo.

    Args:
        hwnd (int): Handle de la ventana a traer al frente.

    Hardcoded:
        - 0.3, 0.5: tiempos de espera antes/después de SetForegroundWindow (TIMING)
    """
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(_SLEEP_MEDIO)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        tid_yo = win32api.GetCurrentThreadId()
        tid_el = win32process.GetWindowThreadProcessId(hwnd)[0]
        ctypes.windll.user32.AttachThreadInput(tid_yo, tid_el, True)
        win32gui.SetForegroundWindow(hwnd)
        ctypes.windll.user32.AttachThreadInput(tid_yo, tid_el, False)
    time.sleep(_SLEEP_LARGO)


def _control_lista_logon(hwnd_logon):
    """Encuentra el control de lista de conexiones en SAP Logon.

    Busca entre los controles hijo el de mayor área que supere las
    dimensiones mínimas (descarta controles pequeños como botones).

    Args:
        hwnd_logon (int): HWND de la ventana SAP Logon.

    Returns:
        tuple[int | None, tuple | None]: (hwnd_control, rect_control) o (None, None).

    Hardcoded:
        - 150, 80: dimensiones mínimas del control de lista (NÚMERO MÁGICO)
    """
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
    """Detecta y cierra popup de error de conexión SAP.

    Busca una ventana con el título de error SAP y la cierra con Escape.

    Returns:
        bool: True si se encontró y cerró un popup de error, False si no había.

    Hardcoded:
        - _TITULO_POPUP_SAP_ERR = "SAP GUI for Windows": título del popup (STRING)
        - 'escape': tecla para cerrar (STRING)
        - 0.3, 0.5: tiempos de espera (TIMING)
    """
    import pyautogui
    hwnd = _hwnd_con_titulo(_TITULO_POPUP_SAP_ERR)
    if not hwnd:
        return False
    texto = win32gui.GetWindowText(hwnd)
    print(f"  [!] Popup de error SAP detectado: {texto!r}")
    try:
        _traer_al_frente(hwnd)
        time.sleep(_SLEEP_MEDIO)
        pyautogui.press('escape')
        time.sleep(_SLEEP_LARGO)
    except Exception:
        pass
    return True


def _sesion_activa() -> bool:
    """Verifica si hay una sesión SAP GUI abierta (cualquier ventana SAP).

    Returns:
        bool: True si existe al menos una ventana SAP_FRONTEND_SESSION visible.
    """
    import sap_gui as SAP
    return SAP._encontrar_hwnd() is not None


def _en_pantalla_login() -> bool:
    """Detecta si SAP está mostrando la pantalla de login (título vacío o muy corto).

    Returns:
        bool: True si el título actual corresponde a la pantalla de login SAP.

    Hardcoded:
        - ("sap",): títulos considerados como pantalla de login (STRING)
        - 4: longitud máxima de título de pantalla de login (NÚMERO MÁGICO)
    """
    import sap_gui as SAP
    t = _sin_tildes(SAP.titulo_actual()).strip()
    return not t or t in ("sap",) or len(t) <= 4


def _mover_ventana_origen(hwnd):
    """Mueve una ventana al origen (0,0) manteniendo su tamaño.

    Args:
        hwnd (int): Handle de la ventana a mover.

    Hardcoded:
        - 0, 0: coordenadas de destino (CONFIG — calibradas para resolución actual)
        - 0.2: tiempo de espera tras mover (TIMING)
    """
    r = win32gui.GetWindowRect(hwnd)
    w, h = r[2] - r[0], r[3] - r[1]
    win32gui.MoveWindow(hwnd, 0, 0, w, h, True)
    time.sleep(_SLEEP_CORTO)


def abrir_sap_logon():
    """Abre SAP Logon 64 si no está abierto, o lo trae al frente si ya está.

    Espera hasta _TIMEOUT_LOGON_EXE segundos a que aparezca la ventana
    después de lanzar el ejecutable.

    Returns:
        int: HWND de la ventana SAP Logon.

    Raises:
        RuntimeError: Si SAP Logon no responde en _TIMEOUT_LOGON_EXE segundos.

    Hardcoded:
        - SAPLOGON: ruta al ejecutable (PATH — configurable en main.py)
        - _TIMEOUT_LOGON_EXE = 20: segundos de espera (TIMING)
        - 1.0: espera adicional tras detectar ventana (TIMING)
    """
    hwnd = _hwnd_logon()
    if hwnd:
        print("  SAP Logon 64 ya está abierto.")
        _traer_al_frente(hwnd)
        _mover_ventana_origen(hwnd)
        return hwnd

    print("  Lanzando SAP Logon 64...", end="", flush=True)
    subprocess.Popen([SAPLOGON])
    for _ in range(_TIMEOUT_LOGON_EXE):
        time.sleep(_SLEEP_POLL)
        print(".", end="", flush=True)
        hwnd = _hwnd_logon()
        if hwnd:
            break
    print()
    if not hwnd:
        raise RuntimeError(f"SAP Logon no respondió en {_TIMEOUT_LOGON_EXE} s.")
    time.sleep(_SLEEP_POLL)
    _traer_al_frente(hwnd)
    _mover_ventana_origen(hwnd)
    print("  SAP Logon abierto.")
    return hwnd


def conectar_ps4(hwnd_logon):
    """Conecta al sistema PS4 PRODUCCION en SAP Logon via type-ahead.

    Hace clic en la lista de conexiones y escribe 'p' + Enter para
    seleccionar PS4 PRODUCCION por type-ahead (primera entrada que empieza con P).

    Args:
        hwnd_logon (int): HWND de la ventana SAP Logon.

    Hardcoded:
        - _CLICK_OFFSET_Y_LOGON = 80: offset Y para clic en la lista (NÚMERO MÁGICO)
        - "p": letra inicial de PS4 para type-ahead (STRING — depende del nombre del sistema)
        - 0.4, 0.3: tiempos de espera (TIMING)
    """
    import pyautogui
    _traer_al_frente(hwnd_logon)
    _, rect_lista = _control_lista_logon(hwnd_logon)
    if rect_lista:
        cx = (rect_lista[0] + rect_lista[2]) // 2
        cy = rect_lista[1] + _CLICK_OFFSET_Y_LOGON
        pyautogui.click(cx, cy)
        print(f"  Clic en lista SAP Logon ({cx},{cy})")
    else:
        r = win32gui.GetWindowRect(hwnd_logon)
        pyautogui.click((r[0]+r[2])//2, (r[1]+r[3])//2)
        print("  Clic en centro de ventana (fallback)")
    time.sleep(_SLEEP_LARGO)
    pyautogui.press("p")
    time.sleep(_SLEEP_MEDIO)
    pyautogui.press("enter")
    print("  Enter → PS4 PRODUCCION (type-ahead)")


def esperar_sesion(timeout=25) -> bool:
    """Espera hasta que SAP abra una sesión (cualquier ventana SAP_FRONTEND_SESSION).

    Sondea cada 1 segundo durante el timeout indicado.

    Args:
        timeout (int): Segundos máximos de espera. Default: 25.

    Returns:
        bool: True si apareció una sesión SAP, False si se agotó el timeout.
    """
    print("  Esperando pantalla de login SAP...", end="", flush=True)
    for _ in range(timeout):
        time.sleep(_SLEEP_POLL)
        print(".", end="", flush=True)
        if _sesion_activa():
            print()
            return True
    print()
    return False


def llenar_credenciales(pasos_popup: int = 6):
    """Llena las credenciales SAP (mandante, usuario, contraseña) y envía Enter.

    Intenta primero via SAP Scripting (más fiable, no depende de posición del cursor).
    Si no está disponible, usa teclado con campo_ctrlA.
    Maneja automáticamente el popup de sesión múltiple y el popup de "Login correcto".
    Verifica que Caps Lock esté desactivado antes de escribir la contraseña.

    Args:
        pasos_popup (int): Repeticiones de Ctrl+Shift+Down para el popup de sesión
            múltiple (Flujo 1). Ver `_manejar_popup_sesion_multiple`.

    Raises:
        RuntimeError: Si las credenciales no están en .env, o si Caps Lock no se pudo desactivar.

    Hardcoded:
        - win32con.VK_CAPITAL: código virtual de Caps Lock (CONSTANTE Win32)
        - 0x14: código VK_CAPITAL para keybd_event (NÚMERO MÁGICO Win32)
        - "wnd[0]/usr/txtRSYST-MANDT": ID campo Mandante SAP (STRING SAP)
        - "wnd[0]/usr/txtRSYST-BNAME": ID campo Usuario SAP (STRING SAP)
        - "wnd[0]/usr/pwdRSYST-BCODE": ID campo Contraseña SAP (STRING SAP)
        - sendVKey(0): Enter en SAP Scripting (NÚMERO MÁGICO SAP)
        - 2.0, 1.5, 1.0, 0.2, 0.15: tiempos de espera (TIMING)
    """
    import sap_gui as SAP
    import pyautogui
    if not USUARIO or not PASSWORD:
        raise RuntimeError("SAP_USUARIO y SAP_PASSWORD no están en el .env")
    if win32api.GetKeyState(win32con.VK_CAPITAL) & 1:
        print("  [!] Caps Lock activado — desactivando antes de escribir credenciales...")
        ctypes.windll.user32.keybd_event(0x14, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x14, 0, 0x0002, 0)
        time.sleep(_SLEEP_MICRO)
        if win32api.GetKeyState(win32con.VK_CAPITAL) & 1:
            raise RuntimeError("No se pudo desactivar Caps Lock. Hazlo manualmente y reintenta.")
        print("  [✓] Caps Lock desactivado.")
    print("  Llenando credenciales...")
    SAP.activar()
    SAP.mover_a_origen()
    time.sleep(_SLEEP_LARGO)

    _llenado_por_scripting = False
    try:
        sap_auto = win32com.client.GetObject("SAPGUI")
        session  = sap_auto.GetScriptingEngine.Children(0).Children(0)
        for fid in ("wnd[0]/usr/txtRSYST-MANDT",):
            try: session.findById(fid).text = MANDANTE
            except Exception: pass
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
        session.findById("wnd[0]/usr/pwdRSYST-BCODE").text = PASSWORD
        session.findById("wnd[0]").sendVKey(0)  # Enter
        _llenado_por_scripting = True
        _log.info("Credenciales enviadas via scripting.")
        print("  Credenciales enviadas via scripting.")
    except Exception as _e:
        _log.debug(f"Scripting login no disponible ({_e}) — usando teclado.")

    if not _llenado_por_scripting:
        SAP.activar()
        SAP.campo_ctrlA(USUARIO)
        SAP.tab(1)
        SAP.escribir_pynput(PASSWORD)
        time.sleep(_SLEEP_CORTO)
        SAP.activar()
        SAP.enter()
        _log.info("Credenciales enviadas via teclado.")
        print("  Credenciales enviadas via teclado.")

    time.sleep(_SLEEP_INICIO)
    _manejar_popup_sesion_multiple(pasos=pasos_popup)
    time.sleep(_SLEEP_SAP)
    try:
        SAP.activar(); SAP.enter(); time.sleep(_SLEEP_POLL)
    except Exception:
        pass


def hacer_login():
    """Gestiona el ciclo completo de apertura de SAP y login.

    Escenarios manejados:
    - SAP ya tiene sesión activa en menú principal → navega a Easy Access.
    - SAP está en pantalla de login → llena credenciales directamente.
    - SAP no está abierto → lanza SAP Logon, conecta PS4, espera sesión, llena credenciales.
    - Primer intento fallido → reintenta conectar PS4.

    Raises:
        RuntimeError: Si no se puede conectar a PS4 o las credenciales son incorrectas.

    Hardcoded:
        - _TIMEOUT_SESION = 25: espera primera conexión (TIMING)
        - _TIMEOUT_SESION_R2 = 20: espera segundo intento (TIMING)
        - _TIMEOUT_LOGIN_VERIFY = 10: reintentos de verificación (NÚMERO MÁGICO)
        - "/n": comando SAP de navegación a Easy Access (STRING SAP)
    """
    import sap_gui as SAP
    if _sesion_activa() and not _en_pantalla_login():
        print("  Sesión SAP activa — cerrando para iniciar sesión limpia...")
        try:
            SAP.cerrar_sap()
            time.sleep(_SLEEP_INICIO)
        except Exception:
            pass
        print("  Sesión cerrada — abriendo nueva sesión.")

    if not (_sesion_activa() and _en_pantalla_login()):
        hwnd_logon = abrir_sap_logon()
        conectar_ps4(hwnd_logon)

        if not esperar_sesion(timeout=_TIMEOUT_SESION):
            print("  Primer intento fallido — reintentando...")
            _traer_al_frente(hwnd_logon)
            _, rect_lista = _control_lista_logon(hwnd_logon)
            if rect_lista:
                import pyautogui
                cx = (rect_lista[0] + rect_lista[2]) // 2
                cy = rect_lista[1] + _CLICK_OFFSET_Y_LOGON
                pyautogui.click(cx, cy)
                time.sleep(_SLEEP_MEDIO)
                pyautogui.press("p")
                time.sleep(_SLEEP_MEDIO)
                pyautogui.press("enter")
            if not esperar_sesion(timeout=_TIMEOUT_SESION_R2):
                raise RuntimeError("No se pudo conectar a PS4. Revisa SAP Logon.")
    else:
        print("  Pantalla de login detectada — SAP ya estaba abierto.")

    llenar_credenciales(pasos_popup=_POPUP_SESION_PASOS)

    print("  Verificando login...", end="", flush=True)
    for _ in range(_TIMEOUT_LOGIN_VERIFY):
        time.sleep(_SLEEP_POLL)
        print(".", end="", flush=True)
        if not _en_pantalla_login():
            break
    print()

    if _en_pantalla_login():
        # Aún en login: probable popup de sesión múltiple mal navegado
        # (Ctrl+Shift+Down×N no alcanzó "Continuar", o cayó en otra opción).
        # Reintentar una vez con el valor alterno antes de asumir credenciales incorrectas.
        _log.warning("Sigue en pantalla de login tras %d pasos — reintentando popup con %d.",
                     _POPUP_SESION_PASOS, _POPUP_SESION_PASOS_RETRY)
        print(f"  [!] Sigue en pantalla de login — reintentando popup con {_POPUP_SESION_PASOS_RETRY} pasos...")
        llenar_credenciales(pasos_popup=_POPUP_SESION_PASOS_RETRY)
        print("  Verificando login...", end="", flush=True)
        for _ in range(_TIMEOUT_LOGIN_VERIFY):
            time.sleep(_SLEEP_POLL)
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
    """Carga la lista de bancos configurados desde bancos.json.

    Returns:
        list[dict]: Lista de dicts de bancos con nombre, cuenta_mayor_sap, etc.

    Raises:
        FileNotFoundError: Si bancos.json no existe en _BASE.

    Hardcoded:
        - BANCOS_FILE: ruta al archivo (PATH — construida con _BASE)
        - "bancos": clave raíz en el JSON (STRING)
        - "utf-8": encoding del archivo (STRING)
    """
    with open(BANCOS_FILE, encoding="utf-8") as f:
        return json.load(f)["bancos"]


def fechas_mes_actual():
    """Calcula el primer y último día del período en formato SAP (DD.MM.YYYY).

    Lee MES_ANTERIOR del .env: "1" → mes anterior, cualquier otro valor → mes actual.

    Returns:
        tuple[str, str]: (fecha_desde, fecha_hasta) del período seleccionado.
    """
    hoy = date.today()
    if os.getenv("MES_ANTERIOR", "0") == "1":
        año, mes = (hoy.year - 1, 12) if hoy.month == 1 else (hoy.year, hoy.month - 1)
    else:
        año, mes = hoy.year, hoy.month
    primero = date(año, mes, 1).strftime("%d.%m.%Y")
    ultimo  = date(año, mes, calendar.monthrange(año, mes)[1]).strftime("%d.%m.%Y")
    return primero, ultimo


# ─────────────────────────────────────────────────────────────
# Procesamiento
# ─────────────────────────────────────────────────────────────
def escribir_valores_bancos_esperados(banco: dict, fecha_desde: str, fecha_hasta: str):
    """Escribe los valores esperados para validar ZFIEC015. No deben ser pisados por OCR."""
    import json as _json
    import pathlib as _pathlib

    ruta = _pathlib.Path(_BASE) / "valores_bancos.json"

    try:
        base = _json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
    except Exception:
        base = {}

    base.update({
        "Sociedad":                 os.getenv("SAP_SOCIEDAD", ""),
        "Proveedor":                banco["cuenta_mayor_sap"],
        "FechaInicio":              fecha_desde,
        "FechaFin":                 fecha_hasta,
        "Código Tipo de Documento": os.getenv("TIPO_DOC_ZFIEC", ""),
        "Texto Cabecera":           banco["texto_cabecera"],
    })

    ruta.write_text(
        _json.dumps(base, ensure_ascii=False, indent=4),
        encoding="utf-8"
    )

    _log.info(
        "valores_bancos.json esperado actualizado: Proveedor=%r FechaInicio=%r FechaFin=%r TipoDoc=%r",
        base.get("Proveedor"),
        base.get("FechaInicio"),
        base.get("FechaFin"),
        base.get("Código Tipo de Documento"),
    )

def _hacer_on_batch(notif, banco: dict, nombre: str):
    """Crea el callback on_batch que procesar_documentos invoca cada N registros.

    Envía el correo con SOLO los registros nuevos del lote (no repite los ya
    enviados). Un fallo de correo se registra como warning y NUNCA interrumpe
    el procesamiento — mismo criterio que _notificar().

    Args:
        notif: Instancia de NotificadorSAP, o None si las notificaciones están deshabilitadas.
        banco (dict): Configuración del banco (para _build_registros).
        nombre (str): Nombre del banco, para el asunto/cuerpo del correo.

    Returns:
        callable | None: on_batch(procesados_nuevos, errores_nuevos), o None si notif es None.
    """
    if not notif:
        return None

    def on_batch(procesados_nuevos: list, errores_nuevos: list) -> None:
        if not procesados_nuevos and not errores_nuevos:
            return
        _notificar(notif, nombre, procesados_nuevos, errores_nuevos,
                   _build_registros(banco, procesados_nuevos, errores_nuevos))

    return on_batch


def procesar_banco(banco: dict, fecha_desde: str, fecha_hasta: str, max_docs: int = None, notif=None):
    """Busca documentos pendientes en ZFIEC015 y procesa cada uno en FB60.

    Imprime un encabezado con el nombre del banco y período,
    llama a buscar() y si hay resultados llama a procesar_documentos().

    El envío de correo ocurre DENTRO de procesar_documentos, en lotes de
    _EMAIL_BATCH_SIZE registros (incluye el lote final aunque sea parcial) —
    el llamador (main()) ya NO envía un correo aparte al final para evitar
    duplicar lo que el callback on_batch ya mandó.

    Args:
        banco (dict): Configuración del banco (nombre, cuenta_mayor_sap, etc.).
        fecha_desde (str): Fecha inicio del período en formato DD.MM.YYYY.
        fecha_hasta (str): Fecha fin del período en formato DD.MM.YYYY.
        max_docs (int | None): Máximo de documentos a procesar. None = sin límite.
        notif: Instancia de NotificadorSAP, o None si las notificaciones están deshabilitadas.

    Returns:
        tuple[list, list]: (procesados, errores) de procesar_documentos().

    Hardcoded:
        - "─" * 55: separador visual en consola (ESTILO)
        - "cuenta_mayor_sap": clave del proveedor SAP en el dict banco (STRING)
    """
    from transactions.zfiec015_kb import buscar, procesar_documentos
    import json as _json, pathlib as _pathlib

    print(f"\n{'─'*55}")
    print(f"  Banco: {banco['nombre']}  |  Proveedor: {banco['cuenta_mayor_sap']}")
    print(f"  Período: {fecha_desde} al {fecha_hasta}")
    print(f"{'─'*55}")

    # Escribir valores esperados iniciales para ZFIEC015
    escribir_valores_bancos_esperados(banco, fecha_desde, fecha_hasta)


    # Actualizar valores_fb60.json con campos contables del banco/período actual.
    # El resto de campos (Titulo, Clase doc, etc.) se preservan del JSON existente.
    _ruta_fb60 = _pathlib.Path(_BASE) / "valores_fb60.json"
    try:
        _base_fb60 = _json.loads(_ruta_fb60.read_text(encoding="utf-8")) if _ruta_fb60.exists() else {}
    except Exception:
        _base_fb60 = {}
    _base_fb60.update({
        "Clase documento": os.getenv("FB60_CLASE_DOCUMENTO", "Factura acreedor"),
        "Combo B2":        os.getenv("INDICADOR_IMPUESTO", "B2"),
        "Cta.mayor":       banco["cuenta_mayor"],
        "Centro coste":    banco["centro_costo"],
        "Texto":           banco["texto_comision"],
    })
    # Las fechas las valida el OCR cruzando factura vs contab. — no deben estar en el JSON esperado
    _base_fb60.pop("Fecha factura", None)
    _base_fb60.pop("Fecha contab.", None)
    _ruta_fb60.write_text(_json.dumps(_base_fb60, ensure_ascii=False, indent=4), encoding="utf-8")


    count = None

    for _intento in range(_MAX_INTENTOS_BANCO):
        try:
            escribir_valores_bancos_esperados(banco, fecha_desde, fecha_hasta)

            _ruta_debug_vb = _pathlib.Path(_BASE) / "valores_bancos.json"

            try:
                _debug_vb = _json.loads(_ruta_debug_vb.read_text(encoding="utf-8"))
            except Exception as _e:
                _debug_vb = {"_error": str(_e)}

            _log.info(
                "Antes de buscar intento %d/%d — valores_bancos.json: %r",
                _intento + 1,
                _MAX_INTENTOS_BANCO,
                _debug_vb
            )

            count = buscar(banco["cuenta_mayor_sap"], fecha_desde, fecha_hasta)
            break

        except RuntimeError as _e:
            if _intento < _MAX_INTENTOS_BANCO - 1:
                print(f"  ↺ Validación fallida — reintento {_intento + 1}/{_MAX_INTENTOS_BANCO - 1}...")
                _log.warning("buscar intento %d fallido: %s", _intento + 1, _e)
                time.sleep(_SLEEP_REINTENTO_BANCO)
            else:
                _log.error("buscar fallido tras %d intentos: %s", _MAX_INTENTOS_BANCO, _e, exc_info=True)
                raise


    if count == 0:
        print("  Sin documentos pendientes.")
        return [], []

    return procesar_documentos(
        banco, fecha_desde=fecha_desde, fecha_hasta=fecha_hasta, max_docs=max_docs,
        on_batch=_hacer_on_batch(notif, banco, banco["nombre"]),
        batch_size=_EMAIL_BATCH_SIZE,
    )


def _notificar(notif, banco: str, procesados: list, errores: list, registros: list) -> None:
    """Envía correo de resumen del banco procesado. Registra warning si falla.

    No interrumpe el flujo principal si el envío de correo falla.

    Args:
        notif: Instancia de NotificadorSAP, o None si las notificaciones están deshabilitadas.
        banco (str): Nombre del banco.
        procesados (list): Lista de documentos procesados exitosamente.
        errores (list): Lista de errores ocurridos.
        registros (list): Lista de dicts con formato para el correo.
    """
    if not notif:
        return
    try:
        notif.notify_resumen_banco(banco, registros)
    except Exception as e:
        _log.warning("Correo de resumen no enviado (%s): %s", banco, e)
        print(f"  [!] Correo no enviado ({banco}): {e}")


def _notificar_error(notif, banco: str, error: str) -> None:
    """Envía correo de error crítico de un banco. Registra warning si falla.

    No interrumpe el flujo principal si el envío de correo falla.

    Args:
        notif: Instancia de NotificadorSAP, o None si deshabilitado.
        banco (str): Nombre del banco donde ocurrió el error.
        error (str): Mensaje o traceback del error.
    """
    if not notif:
        return
    try:
        notif.notify_error_banco(banco, error)
    except Exception as e:
        _log.warning("Correo de error no enviado (%s): %s", banco, e)
        print(f"  [!] Correo de error no enviado ({banco}): {e}")


def imprimir_resumen(resultados: list):
    """Imprime el resumen final de todos los bancos procesados en consola.

    Args:
        resultados (list[dict]): Lista de dicts con banco, procesados y errores.

    Hardcoded:
        - "═" * 55, "─" * 55: separadores visuales (ESTILO)
        - "sap_doc", "doc", "error": claves esperadas en los dicts (STRING)
    """
    print(f"\n{'═'*55}")
    print("  RESUMEN FINAL")
    print(f"{'═'*55}")
    total_ok  = sum(len(r["procesados"]) for r in resultados)
    total_err = sum(len(r["errores"])    for r in resultados)
    for r in resultados:
        print(f"  {r['banco']:20s}  ✓ {len(r['procesados'])} ok   ✗ {len(r['errores'])} errores")
        for p in r["procesados"]:
            print(f"    ✓ Doc SAP: {p['sap_doc']}  ({p['doc']})" if 'doc' in p
                  else f"    ✓ Doc SAP: {p['sap_doc']}")
        for e in r["errores"]:
            print(f"    ✗ {e['doc']}: {e['error']}")
    print(f"{'─'*55}")
    print(f"  Total: {total_ok} contabilizados, {total_err} con error")
    print(f"{'═'*55}\n")


def _build_registros(banco: dict, procesados: list, errores: list) -> list:
    """Construye la lista de registros para el correo de notificación.

    Combina los documentos procesados y los errores en un formato uniforme
    compatible con NotificadorSAP.notify_resumen_banco.

    Args:
        banco (dict): Configuración del banco (cuenta_mayor, centro_costo).
        procesados (list[dict]): Resultados exitosos de registrar_factura.
        errores (list[dict]): Errores con claves "doc" y "error".

    Returns:
        list[dict]: Lista combinada con claves:
            numero_doc, fecha, importe, cuenta_mayor, centro_costo, estado, detalle,
            ocr_basico, ocr_pago, ocr_detalle (estos tres solo presentes en registros
            CONTABILIZADO — dicts con los valores detectados por OCR en cada pestaña).

    Hardcoded:
        - "CONTABILIZADO", "ERROR": valores de estado (STRING)
    """
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
            "ocr_basico":   p.get("ocr_basico", {}),
            "ocr_pago":     p.get("ocr_pago", {}),
            "ocr_detalle":  p.get("ocr_detalle", {}),
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

def _validar_entorno():
    """Valida y prepara el entorno antes de iniciar el procesamiento.

    Pasos:
    1. Detectar y cerrar popups de error de conexión SAP.
    2. Verificar/desactivar Caps Lock.
    3. Abrir SAP y hacer login automático.
    4. Confirmar que SAP quedó en el menú principal (via Scripting si disponible).

    Raises:
        SystemExit(1): Si hay error de conexión SAP, Caps Lock no se puede desactivar,
                       o SAP quedó en una transacción inesperada.

    Hardcoded:
        - _TCODES_MENU: tcodes válidos del menú principal (CONFIG)
        - win32con.VK_CAPITAL: código de Caps Lock (CONSTANTE Win32)
    """
    if _cerrar_popup_error_sap():
        print("  [!] SAP tiene un error de conexión (WSAECONNRESET u otro).")
        print("      Cierra SAP, verifica la red y vuelve a ejecutar.")
        sys.exit(1)

    if win32api.GetKeyState(win32con.VK_CAPITAL) & 1:
        print("  [!] Caps Lock activado — desactivando automáticamente...")
        ctypes.windll.user32.keybd_event(0x14, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x14, 0, 0x0002, 0)
        time.sleep(_SLEEP_MICRO)
        if win32api.GetKeyState(win32con.VK_CAPITAL) & 1:
            print("  [!] No se pudo desactivar Caps Lock. Hazlo manualmente y vuelve a ejecutar.")
            sys.exit(1)
        print("  [✓] Caps Lock desactivado.")
    else:
        print("  [✓] Caps Lock inactivo.")

    hacer_login()

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
# Main
# ─────────────────────────────────────────────────────────────

def main():
    """Punto de entrada principal del proceso de comisiones bancarias.

    Flujo completo:
    1. Validar entorno (SAP abierto, login correcto).
    2. Cargar bancos con proveedor configurado.
    3. Calcular período del mes actual.
    4. Por cada banco: buscar en ZFIEC015 y registrar en FB60.
    5. Enviar notificaciones por correo (resumen o error).
    6. Imprimir resumen final y cerrar SAP.

    """
    print("╔══════════════════════════════════════════════════════╗")
    print("║   SAP Automatización – Comisiones Bancarias          ║")
    print("║   ASIAUTO S.A.                                       ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()

    # Verificar Tesseract antes de abrir SAP
    try:
        from transactions.validacion_pantalla import verificar_tesseract
        if not verificar_tesseract():
            print("  [!] Tesseract OCR no está operativo.")
            print("      Instala Tesseract-OCR o usa el ejecutable con OCR embebido.")
            print("      Descarga: https://github.com/UB-Mannheim/tesseract/wiki")
            if getattr(sys, "frozen", False):
                input("\n  Presiona Enter para cerrar...")
            sys.exit(1)
        print("  [✓] Tesseract OCR operativo.")
    except (ImportError, SystemExit) as _e:
        if isinstance(_e, SystemExit):
            raise
        print(f"  [!] No se pudo verificar Tesseract: {_e}")
        if getattr(sys, "frozen", False):
            input("\n  Presiona Enter para cerrar...")
        sys.exit(1)

    _validar_entorno()

    bancos = cargar_bancos()
    bancos_a_procesar = sorted(
        [b for b in bancos if b["cuenta_mayor_sap"]],
        key=lambda b: b.get("orden", 99)
    )
    if not bancos_a_procesar:
        print("  No hay bancos con proveedor configurado en bancos.json")
        sys.exit(1)

    fecha_desde, fecha_hasta = fechas_mes_actual()

    print(f"\n  Bancos  : {', '.join(b['nombre'] for b in bancos_a_procesar)}")
    print(f"  Período : {fecha_desde} – {fecha_hasta}")
    print()

    max_docs = None   # siempre procesa todos los documentos

    try:
        from correos.notificador_sap import NotificadorSAP
        notif = NotificadorSAP()
        print("  [✓] Notificaciones por correo habilitadas.")
    except Exception as e:
        _log.warning(f"Notificaciones deshabilitadas: {e}")
        print(f"  [!] Notificaciones deshabilitadas: {e}")
        notif = None

    # Inicializar "Texto Cabecera" en valores_bancos.json antes de iterar bancos
    import pathlib as _pl_init
    _ruta_vb = _pl_init.Path(_BASE) / "valores_bancos.json"
    try:
        _vb = json.loads(_ruta_vb.read_text(encoding="utf-8")) if _ruta_vb.exists() else {}
    except Exception:
        _vb = {}
    _vb["Texto Cabecera"] = ""
    _ruta_vb.write_text(json.dumps(_vb, ensure_ascii=False, indent=4), encoding="utf-8")

    resultados = []
    for banco in bancos_a_procesar:
        nombre = banco["nombre"]
        for intento in range(1, _MAX_INTENTOS_BANCO + 1):
            if intento > 1:
                _log.warning("REINTENTO %d/%d %s — esperando %.0fs",
                             intento, _MAX_INTENTOS_BANCO, nombre, _SLEEP_REINTENTO_BANCO)
                print(f"  [↺] Reintento {intento}/{_MAX_INTENTOS_BANCO} {nombre}...")
                time.sleep(_SLEEP_REINTENTO_BANCO)
            _log.info("=== Inicio %s %s-%s === (intento %d/%d)",
                      nombre, fecha_desde, fecha_hasta, intento, _MAX_INTENTOS_BANCO)
            try:
                # El correo se envía dentro de procesar_banco/procesar_documentos,
                # en lotes de _EMAIL_BATCH_SIZE (incluye el lote final) — no se
                # repite aquí para no duplicar lo ya enviado.
                procesados, errores = procesar_banco(banco, fecha_desde, fecha_hasta, max_docs=max_docs, notif=notif)
                resultados.append({"banco": nombre, "procesados": procesados, "errores": errores})
                _log.info("%s: procesados=%d errores=%d", nombre, len(procesados), len(errores))
                break   # éxito → siguiente banco
            except Exception as e:
                msg = str(e)
                if intento < _MAX_INTENTOS_BANCO:
                    _log.warning("Intento %d/%d %s: %s", intento, _MAX_INTENTOS_BANCO, nombre, msg)
                    print(f"  [!] Error intento {intento}/{_MAX_INTENTOS_BANCO} {nombre}: {msg}")
                else:
                    _log.error("ERROR CRÍTICO %s tras %d intentos: %s",
                               nombre, _MAX_INTENTOS_BANCO, msg, exc_info=True)
                    print(f"\n  ✗ Error en {nombre}: {msg}")
                    resultados.append({"banco": nombre, "procesados": [],
                                       "errores": [{"doc": "—", "error": msg}]})
                    _notificar_error(notif, nombre, msg)

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
