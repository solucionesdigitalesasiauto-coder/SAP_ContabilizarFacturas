"""
Motor de automatización SAP GUI via teclado/mouse (sin scripting SAP).
Clase ventana: SAP_FRONTEND_SESSION (confirmado con Au3Info).
"""
import time
import unicodedata
import win32gui
import win32con
import win32api
import pyautogui
from pynput.keyboard import Key, Controller as _KbController


def _sin_tildes(s: str) -> str:
    """Elimina tildes/diacriticos para comparaciones insensibles a acentos."""
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii").lower()

_kb = _KbController()

_KEY_MAP = {
    'tab': Key.tab,        'enter': Key.enter,    'space': Key.space,
    'escape': Key.esc,     'esc': Key.esc,        'delete': Key.delete,
    'home': Key.home,      'end': Key.end,
    'down': Key.down,      'up': Key.up,
    'left': Key.left,      'right': Key.right,
    'f1': Key.f1,  'f2': Key.f2,  'f3': Key.f3,  'f4': Key.f4,
    'f5': Key.f5,  'f6': Key.f6,  'f7': Key.f7,  'f8': Key.f8,
    'f9': Key.f9,  'f10': Key.f10, 'f11': Key.f11, 'f12': Key.f12,
    'pagedown': Key.page_down, 'pageup': Key.page_up,
}

def _press(key_name: str):
    """Presiona y suelta una tecla vía pynput (más fiable que pyautogui en SAP)."""
    k = _KEY_MAP.get(str(key_name).lower(), key_name)
    _kb.press(k); time.sleep(0.05); _kb.release(k); time.sleep(0.05)

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.08

SAP_CLASE = "SAP_FRONTEND_SESSION"


# ─────────────────────────────────────────────────────────────
# Ventana SAP
# ─────────────────────────────────────────────────────────────

def _encontrar_hwnd(titulo_contiene=None):
    handles = []
    def cb(hwnd, _):
        if win32gui.GetClassName(hwnd) == SAP_CLASE:
            t = win32gui.GetWindowText(hwnd)
            if titulo_contiene is None or _sin_tildes(titulo_contiene) in _sin_tildes(t):
                handles.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return handles[0] if handles else None


def activar(titulo_contiene=None):
    """Trae la ventana SAP al frente. Lanza error si no la encuentra."""
    hwnd = _encontrar_hwnd(titulo_contiene)
    if not hwnd:
        raise RuntimeError(
            f"Ventana SAP no encontrada"
            + (f" con título '{titulo_contiene}'" if titulo_contiene else "")
        )
    # Solo restaurar si está MINIMIZADA — SW_RESTORE sobre una ventana maximizada
    # la achica a tamaño "normal", SAP redibuja el formulario y el foco salta
    # al campo incorrecto (además bloquea campos de fecha en edición).
    placement = win32gui.GetWindowPlacement(hwnd)
    if placement[1] == win32con.SW_SHOWMINIMIZED:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        # Workaround: attach thread input para forzar el foco
        import win32process
        import ctypes
        fg = win32gui.GetForegroundWindow()
        if fg and fg != hwnd:
            tid_fg = win32process.GetWindowThreadProcessId(fg)[0]
            tid_sap = win32process.GetWindowThreadProcessId(hwnd)[0]
            ctypes.windll.user32.AttachThreadInput(tid_fg, tid_sap, True)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
            ctypes.windll.user32.AttachThreadInput(tid_fg, tid_sap, False)
    time.sleep(0.4)
    return hwnd


def esperar_titulo(titulo_contiene, timeout=15):
    """Espera hasta que SAP muestre la pantalla esperada."""
    fin = time.time() + timeout
    while time.time() < fin:
        if _encontrar_hwnd(titulo_contiene):
            return True
        time.sleep(0.3)
    raise RuntimeError(f"Pantalla '{titulo_contiene}' no aparecio en {timeout}s")


def verificar_pantalla(titulo_esperado: str, paso: str) -> str:
    """Verifica inmediatamente que la pantalla activa sea la esperada.
    Lanza RuntimeError con mensaje claro si no coincide."""
    import logging
    actual = titulo_actual()
    logger = logging.getLogger(__name__)
    if _sin_tildes(titulo_esperado) not in _sin_tildes(actual):
        msg = (f"[{paso}] Pantalla inesperada — "
               f"esperada: '{titulo_esperado}' | actual: '{actual}'")
        logger.error(msg)
        raise RuntimeError(msg)
    logger.info(f"[{paso}] OK — {actual!r}")
    return actual


def titulo_actual():
    hwnd = _encontrar_hwnd()
    return win32gui.GetWindowText(hwnd) if hwnd else ""


def rect():
    """Retorna (left, top, right, bottom) del área cliente de la ventana SAP."""
    hwnd = _encontrar_hwnd()
    if not hwnd:
        return (0, 0, 0, 0)
    # ClientToScreen da la posición absoluta del área cliente (sin borde/título)
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    cr = win32gui.GetClientRect(hwnd)
    return left, top, left + cr[2], top + cr[3]


def pos_en_ventana(rel_x, rel_y):
    """Convierte coordenada relativa al área cliente SAP en coordenada de pantalla."""
    left, top, _, _ = rect()
    return left + rel_x, top + rel_y


def posicionar_ventana(x=None, y=None, ancho=None, alto=None):
    """Mueve y redimensiona la ventana SAP. Sin argumentos usa SAP_WIN_* del .env."""
    import os
    x     = int(os.getenv("SAP_WIN_X",     "0"))    if x     is None else x
    y     = int(os.getenv("SAP_WIN_Y",     "0"))    if y     is None else y
    ancho = int(os.getenv("SAP_WIN_ANCHO", "1024")) if ancho is None else ancho
    alto  = int(os.getenv("SAP_WIN_ALTO",  "768"))  if alto  is None else alto
    hwnd = _encontrar_hwnd()
    if not hwnd:
        raise RuntimeError("Ventana SAP no encontrada")
    placement = win32gui.GetWindowPlacement(hwnd)
    if placement[1] in (win32con.SW_SHOWMINIMIZED, win32con.SW_SHOWMAXIMIZED):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.2)
    try:
        win32gui.MoveWindow(hwnd, x, y, ancho, alto, True)
    except Exception:
        pass   # modal dialog activo o restricción del proceso — ignorar
    time.sleep(0.4)


def mover_a_origen():
    """Mueve la ventana SAP a (0,0) SIN cambiar su tamaño.
    Necesario antes de clics con coordenadas calibradas en posición (0,0)."""
    hwnd = _encontrar_hwnd()
    if not hwnd:
        return
    placement = win32gui.GetWindowPlacement(hwnd)
    if placement[1] == win32con.SW_SHOWMINIMIZED:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    r = win32gui.GetWindowRect(hwnd)
    w, h = r[2] - r[0], r[3] - r[1]
    win32gui.MoveWindow(hwnd, 0, 0, w, h, True)
    time.sleep(0.2)


def info_ventana():
    """Imprime posición de la ventana para diagnóstico."""
    hwnd = _encontrar_hwnd()
    if not hwnd:
        print("  Ventana SAP no encontrada")
        return
    outer = win32gui.GetWindowRect(hwnd)
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    cr = win32gui.GetClientRect(hwnd)
    print(f"  GetWindowRect (marco completo): {outer}")
    print(f"  ClientToScreen(0,0):            ({left}, {top})")
    print(f"  GetClientRect (tamaño interno): {cr}")


# ─────────────────────────────────────────────────────────────
# Teclado
# ─────────────────────────────────────────────────────────────

def escribir(texto, delay=0.05):
    pyautogui.write(str(texto), interval=delay)


def tecla(key):
    _press(key)


def combo(*keys):
    pyautogui.hotkey(*keys)


def tab(n=1):
    tab_pynput(n)


def tab_pynput(n=1):
    """Tab via pynput — más fiable que pyautogui cuando SAP no recibe el Tab normal."""
    for _ in range(n):
        _kb.press(Key.tab)
        time.sleep(0.05)
        _kb.release(Key.tab)
        time.sleep(0.1)


def shift_tab(n=1):
    for _ in range(n):
        with _kb.pressed(Key.shift):
            _kb.press(Key.tab)
            _kb.release(Key.tab)
        time.sleep(0.1)


def copiar():
    """Copia campo activo al portapapeles (Ctrl+A selecciona dentro del campo + Ctrl+C)."""
    with _kb.pressed(Key.ctrl):
        _kb.press('a'); _kb.release('a')
    time.sleep(0.1)
    with _kb.pressed(Key.ctrl):
        _kb.press('c'); _kb.release('c')
    time.sleep(0.25)


def leer_portapapeles() -> str:
    """Lee el texto del portapapeles con pyperclip."""
    try:
        import pyperclip
        return (pyperclip.paste() or "").strip()
    except Exception:
        return ""


def pegar_como_texto():
    """Lee portapapeles y lo tipea en el campo activo (Ctrl+A para limpiar + write)."""
    texto = leer_portapapeles()
    if texto:
        with _kb.pressed(Key.ctrl):
            _kb.press('a'); _kb.release('a')
        time.sleep(0.05)
        pyautogui.write(texto)


def pegar_fecha():
    """Lee portapapeles y tipea la fecha completa (incluyendo puntos).
    SAP salta los puntos de la máscara automáticamente al tipear."""
    texto = leer_portapapeles()
    if texto:
        with _kb.pressed(Key.ctrl):
            _kb.press('a'); _kb.release('a')
        time.sleep(0.05)
        pyautogui.write(texto, interval=0.05)


def enter():
    _press('enter')


def limpiar():
    """Selecciona todo el campo (Home + Shift+End) y borra con Delete.
    Shift+End selecciona hacia adelante dentro del campo.
    Shift+Home en SAP salta al primer campo del formulario — NO usar."""
    _press('home')
    with _kb.pressed(Key.shift):
        _kb.press(Key.end); _kb.release(Key.end)
    time.sleep(0.05)
    _press('delete')


def campo(texto):
    """Limpia el campo activo y escribe el texto."""
    limpiar()
    escribir(texto)


def campo_ctrlA(texto):
    """Limpia con Ctrl+A + Delete y escribe. Usar en campos ZFIEC015 donde Home no funciona."""
    with _kb.pressed(Key.ctrl):
        _kb.press('a'); _kb.release('a')
    time.sleep(0.05)
    _press('delete')
    time.sleep(0.05)
    escribir(texto)


def campo_fecha(texto):
    """Igual que campo() — los campos de fecha funcionan igual."""
    limpiar()
    escribir(texto)


def f8():
    _press('f8')


def salir_tabla():
    """4x Ctrl+Shift+Tab — sube el foco de la tabla al header de cabecera.
    Desde cabecera funciona Ctrl+Shift+AvPág para cambiar de pestaña."""
    for _ in range(4):
        with _kb.pressed(Key.ctrl):
            with _kb.pressed(Key.shift):
                time.sleep(0.1)
                _kb.press(Key.tab)
                _kb.release(Key.tab)
        time.sleep(0.4)

def siguiente_pestana():
    """Ctrl+Shift+AvPág — avanza a la siguiente pestaña en FB60.
    Usa pynput para mayor fiabilidad con SAP."""
    with _kb.pressed(Key.ctrl):
        with _kb.pressed(Key.shift):
            time.sleep(0.1)
            _kb.press(Key.page_down)
            _kb.release(Key.page_down)
    time.sleep(0.8)

def ctrl_s():
    with _kb.pressed(Key.ctrl):
        _kb.press('s'); _kb.release('s')


def escape():
    _press('escape')


def f3():
    _press('f3')

def f12():
    _press('f12')


# ─────────────────────────────────────────────────────────────
# Mouse
# ─────────────────────────────────────────────────────────────

def click_en(rel_x, rel_y, boton='left'):
    """Clic en coordenada relativa a la ventana SAP."""
    x, y = pos_en_ventana(rel_x, rel_y)
    pyautogui.click(x, y, button=boton)
    time.sleep(0.15)


def doble_click_en(rel_x, rel_y):
    x, y = pos_en_ventana(rel_x, rel_y)
    pyautogui.doubleClick(x, y)
    time.sleep(0.15)


def click_win32(rel_x, rel_y):
    """Clic vía win32api.mouse_event (nivel bajo, alternativa a pyautogui)."""
    x, y = pos_en_ventana(rel_x, rel_y)
    win32api.SetCursorPos((x, y))
    time.sleep(0.1)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
    time.sleep(0.15)


def click_postmsg(grid_rel_x, grid_rel_y, grid_clase="SAPALVGrid"):
    """
    Envía WM_LBUTTONDOWN directamente al control SAPALVGrid hijo.
    grid_rel_x/y son coordenadas DENTRO de la grilla (relativas al control).
    """
    parent = _encontrar_hwnd()
    if not parent:
        raise RuntimeError("Ventana SAP no encontrada")

    # Buscar el control hijo SAPALVGrid
    grid_hwnd = None
    def cb(hwnd, _):
        nonlocal grid_hwnd
        if win32gui.GetClassName(hwnd) == grid_clase:
            grid_hwnd = hwnd
    win32gui.EnumChildWindows(parent, cb, None)

    if not grid_hwnd:
        raise RuntimeError(f"Control {grid_clase} no encontrado dentro de SAP")

    MAKELONG = lambda lo, hi: (lo & 0xFFFF) | ((hi & 0xFFFF) << 16)
    lparam = MAKELONG(grid_rel_x, grid_rel_y)
    win32api.PostMessage(grid_hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
    time.sleep(0.05)
    win32api.PostMessage(grid_hwnd, win32con.WM_LBUTTONUP, 0, lparam)
    time.sleep(0.2)


# ─────────────────────────────────────────────────────────────
# Navegación de transacciones
# ─────────────────────────────────────────────────────────────

def ir_a(tcode):
    """Navega a un código de transacción SAP y fija la ventana en (0,0,1024,768).
    Si el tcode no comienza con '/' se añade '/n' para forzar la navegación."""
    activar()
    combo('ctrl', '/')   # pyautogui.hotkey — Ctrl+/ activa barra de comandos SAP
    time.sleep(0.3)
    # Limpiar barra de comandos con Ctrl+A+Delete (Home salta a Sociedad en ZFIEC015)
    with _kb.pressed(Key.ctrl):
        _kb.press('a'); _kb.release('a')
    time.sleep(0.05)
    _press('delete')
    time.sleep(0.05)
    cmd = tcode if tcode.startswith('/') else f'/n{tcode}'
    escribir(cmd)
    enter()
    time.sleep(1.5)
    posicionar_ventana()


# ─────────────────────────────────────────────────────────────
# Popups
# ─────────────────────────────────────────────────────────────

def confirmar_popup_si():
    """Presiona Enter para confirmar un popup (botón Sí / OK)."""
    time.sleep(0.5)
    enter()
    time.sleep(0.3)


def cancelar_popup():
    time.sleep(0.3)
    escape()
    time.sleep(0.3)


def cerrar_sap():
    """Cierra SAP desde cualquier pantalla.

    Envía SC_CLOSE (equivale al botón X), espera el popup "¿Desea salir del sistema?",
    activa la ventana SAP para que tenga foco, presiona Tab (mueve de "No" a "Sí")
    y Enter para confirmar.
    """
    if not _encontrar_hwnd():
        return
    try:
        import win32com.client

        # ── Intento 1: SAP Scripting (cierra y confirma sin depender del foco) ──
        try:
            app     = win32com.client.GetObject("SAPGUI").GetScriptingEngine
            session = app.Children(0).Children(0)
            _       = session.Type   # valida sesión activa

            session.findById("wnd[0]").close()
            time.sleep(1.2)

            # Confirmar popup "¿Desea salir?" — busca botón Sí por ID estándar
            for btn_id in (
                "wnd[1]/usr/btnSPOP-VAROPTION1",
                "wnd[1]/usr/btn[0]",
                "wnd[1]/tbar[0]/btn[0]",
            ):
                try:
                    session.findById(btn_id).press()
                    time.sleep(1.5)
                    if not _encontrar_hwnd():
                        return
                    break
                except Exception:
                    continue
        except Exception:
            pass   # scripting no disponible → fallback

        if not _encontrar_hwnd():
            return

        # ── Intento 2: SC_CLOSE + hilo Tab+Enter (scripting no disponible) ──
        import threading

        def _confirmar():
            time.sleep(1.5)
            activar()
            time.sleep(0.3)
            tab(1)
            time.sleep(0.25)
            enter()

        hwnd = _encontrar_hwnd()
        activar()
        time.sleep(0.4)
        t = threading.Thread(target=_confirmar, daemon=True)
        t.start()
        win32gui.SendMessage(hwnd, win32con.WM_SYSCOMMAND, win32con.SC_CLOSE, 0)
        t.join(timeout=5)

    except Exception:
        pass
