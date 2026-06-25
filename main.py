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

# ── Timing (ajustar si el sistema es más lento) ──────────────
_SLEEP_MICRO  = 0.15  # micro-pausa (caps lock, pre-state check)
_SLEEP_CORTO  = 0.2   # entre pasos rápidos / pyautogui
_SLEEP_MEDIO  = 0.3   # entre pasos SAP (click, type-ahead)
_SLEEP_LARGO  = 0.5   # tras activar ventana / escape popup
_SLEEP_POLL   = 1     # intervalo de sondeo en loops
_SLEEP_SAP    = 1.5   # tras popup SAP / sesión múltiple
_SLEEP_INICIO = 2.0   # espera SAP arranque / cierre sesión


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


_POPUP_RATIO_OPCION2  = 0.46   # Y: opción 2 "sin finalizar" (~46% altura diálogo)
_POPUP_RATIO_BTN_X   = 0.91   # X: botón ✓ confirmar (~91% ancho diálogo)
_POPUP_RATIO_BTN_Y   = 0.97   # Y: botón ✓ confirmar (~97% altura diálogo)


def _clic_fisico(x: int, y: int) -> None:
    """Clic izquierdo en coordenadas físicas (DPI-safe) vía win32api.
    SetCursorPos posiciona el cursor; mouse_event con 0,0 hace clic ahí.
    """
    win32api.SetCursorPos((x, y))
    time.sleep(_SLEEP_CORTO)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP,   0, 0, 0, 0)


def _manejar_popup_sesion_multiple():
    """Detecta y gestiona el popup de sesión múltiple de SAP.

    Selecciona "Continuar sin finalizar entradas existentes" (opción 2).
    Detecta posición del foco para elegir el flujo de navegación:
      - Foco en texto superior (opción 1 activa) → Down×1 + Enter
      - Foco en tabla inferior                   → Up×3   + Enter
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
    ctypes.windll.user32.SetFocus(hwnd)   # forzar foco en el diálogo
    time.sleep(_SLEEP_SAP)
    ctypes.windll.user32.AttachThreadInput(tid_yo, tid_el, False)

    # Cancelar (opción 3) siempre viene seleccionada al abrirse el diálogo.
    # Up×1 sube a opción 2 "Continuar sin finalizar entradas existentes".
    _kbd = _KbdCtrl()
    _kbd.press(_K.up); _kbd.release(_K.up)
    time.sleep(_SLEEP_LARGO)              # pausa visible antes de confirmar
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

    Returns:
        None

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

    Returns:
        None

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

    Returns:
        None

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


def llenar_credenciales():
    """Llena las credenciales SAP (mandante, usuario, contraseña) y envía Enter.

    Intenta primero via SAP Scripting (más fiable, no depende de posición del cursor).
    Si no está disponible, usa teclado con campo_ctrlA.
    Maneja automáticamente el popup de sesión múltiple y el popup de "Login correcto".
    Verifica que Caps Lock esté desactivado antes de escribir la contraseña.

    Returns:
        None

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
        print("  Credenciales enviadas via scripting.")
    except Exception as _e:
        _log.debug(f"Scripting login no disponible ({_e}) — usando teclado.")

    if not _llenado_por_scripting:
        SAP.activar()
        SAP.campo_ctrlA(USUARIO)
        SAP.tab(1)
        SAP.campo_ctrlA(PASSWORD)
        time.sleep(_SLEEP_CORTO)
        SAP.activar()
        SAP.enter()
        print("  Credenciales enviadas via teclado.")

    time.sleep(_SLEEP_INICIO)
    _manejar_popup_sesion_multiple()
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

    Returns:
        None

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

    llenar_credenciales()

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
    """Calcula el primer y último día del mes actual en formato SAP (DD.MM.YYYY).

    Returns:
        tuple[str, str]: (fecha_desde, fecha_hasta) del mes actual.

    Hardcoded:
        - "%d.%m.%Y": formato de fecha SAP (STRING)
    """
    hoy = date.today()
    primero = hoy.replace(day=1).strftime("%d.%m.%Y")
    ultimo_dia = calendar.monthrange(hoy.year, hoy.month)[1]
    ultimo = hoy.replace(day=ultimo_dia).strftime("%d.%m.%Y")
    return primero, ultimo


# ─────────────────────────────────────────────────────────────
# Procesamiento
# ─────────────────────────────────────────────────────────────

def procesar_banco(banco: dict, fecha_desde: str, fecha_hasta: str, max_docs: int = None):
    """Busca documentos pendientes en ZFIEC015 y procesa cada uno en FB60.

    Imprime un encabezado con el nombre del banco y período,
    llama a buscar() y si hay resultados llama a procesar_documentos().

    Args:
        banco (dict): Configuración del banco (nombre, cuenta_mayor_sap, etc.).
        fecha_desde (str): Fecha inicio del período en formato DD.MM.YYYY.
        fecha_hasta (str): Fecha fin del período en formato DD.MM.YYYY.
        max_docs (int | None): Máximo de documentos a procesar. None = sin límite.

    Returns:
        tuple[list, list]: (procesados, errores) de procesar_documentos().

    Hardcoded:
        - "─" * 55: separador visual en consola (ESTILO)
        - "cuenta_mayor_sap": clave del proveedor SAP en el dict banco (STRING)
    """
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
    """Envía correo de resumen del banco procesado. Registra warning si falla.

    No interrumpe el flujo principal si el envío de correo falla.

    Args:
        notif: Instancia de NotificadorSAP, o None si las notificaciones están deshabilitadas.
        banco (str): Nombre del banco.
        procesados (list): Lista de documentos procesados exitosamente.
        errores (list): Lista de errores ocurridos.
        registros (list): Lista de dicts con formato para el correo.

    Returns:
        None
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

    Returns:
        None
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

    Returns:
        None

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
            numero_doc, fecha, importe, cuenta_mayor, centro_costo, estado, detalle.

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

    Returns:
        None

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

    Returns:
        None

    Hardcoded:
        - "START_FROM_BANCO": variable de entorno para saltar bancos (STRING)
        - "MAX_DOCS_BANCO", "CONTABILIZAR": variables de control (STRING)
        - "orden", 99: clave y default de orden de procesamiento (STRING / NÚMERO MÁGICO)
        - "═" * 56: bordes del banner de inicio (ESTILO)
    """
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

    max_docs = None   # siempre procesa todos los documentos
    _modo = "REAL (contabiliza)" if os.getenv("CONTABILIZAR", "0") == "1" else "PRUEBA (sin guardar)"
    print(f"  Docs/banco : Todos  |  Modo: {_modo}")
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
