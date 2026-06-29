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


# ── Configuración global ──────────────────────────────────────
SAP_CLASE    = "SAP_FRONTEND_SESSION"   # clase de ventana SAP GUI en Win32
_PAUSE_PYAG    = 0.08   # pyautogui.PAUSE global (entre acciones pyautogui)
_SLEEP_CORTO   = 0.1    # entre teclas / clics / tabs
_SLEEP_MEDIO   = 0.3    # entre pasos SAP / clipboard / ventana
_SLEEP_LARGO   = 0.5    # tras activar ventana / espera popup
_SLEEP_PESTANA = 0.8    # tras cambio de pestaña FB60
_SLEEP_TCODE   = 1.5    # tras navegar a transacción / cerrar scripting
_SLEEP_NEND    = 2.0    # tras /nend antes de popup salida
_INTERVAL_WRITE = 0.05  # pausa entre caracteres en pyautogui.write / pegar_fecha

pyautogui.FAILSAFE = True
pyautogui.PAUSE = _PAUSE_PYAG

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


def _sin_tildes(s: str) -> str:
    """Normaliza un string eliminando tildes y diacríticos, en minúsculas.

    Usado para comparaciones insensibles a acentos en títulos de ventana SAP,
    que pueden variar según el idioma/codificación del sistema.

    Args:
        s (str): Texto a normalizar.

    Returns:
        str: Texto sin tildes, en minúsculas.
    """
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii").lower()


def _press(key_name: str):
    """Presiona y suelta una tecla vía pynput con pausa entre press/release.

    Usa pynput en lugar de pyautogui porque SAP necesita que la tecla
    se mantenga presionada el tiempo suficiente para que su message loop la procese.

    Args:
        key_name (str): Nombre de tecla (ej. 'enter', 'tab', 'f8') o carácter.

    Returns:
        None

    Hardcoded:
        - _SLEEP_CORTO = 0.05: pausa entre press y release (TIMING)
    """
    k = _KEY_MAP.get(str(key_name).lower(), key_name)
    _kb.press(k); time.sleep(_SLEEP_CORTO); _kb.release(k); time.sleep(_SLEEP_CORTO)


# ─────────────────────────────────────────────────────────────
# Ventana SAP
# ─────────────────────────────────────────────────────────────

def _encontrar_hwnd(titulo_contiene=None):
    """Busca el HWND de la ventana SAP GUI activa.

    Enumera todas las ventanas del sistema buscando la clase SAP_FRONTEND_SESSION.
    Si se provee titulo_contiene, filtra por título (sin tilde, case-insensitive).

    Args:
        titulo_contiene (str | None): Fragmento de título a buscar, o None para cualquiera.

    Returns:
        int | None: Handle de ventana (HWND) o None si no se encontró.

    Hardcoded:
        - SAP_CLASE = "SAP_FRONTEND_SESSION": clase Win32 de SAP GUI (STRING)
    """
    handles = []
    def cb(hwnd, _):
        if win32gui.GetClassName(hwnd) == SAP_CLASE:
            t = win32gui.GetWindowText(hwnd)
            if titulo_contiene is None or _sin_tildes(titulo_contiene) in _sin_tildes(t):
                handles.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return handles[0] if handles else None


def activar(titulo_contiene=None):
    """Trae la ventana SAP al frente y le da el foco.

    Solo restaura si está minimizada — SW_RESTORE sobre una ventana maximizada
    la redimensiona y SAP redibuja el formulario (campos pierden foco).
    Si SetForegroundWindow falla, usa AttachThreadInput como workaround Win32.

    Args:
        titulo_contiene (str | None): Fragmento de título para identificar la ventana,
                                      o None para activar cualquier ventana SAP.

    Returns:
        int: HWND de la ventana activada.

    Raises:
        RuntimeError: Si no se encuentra ninguna ventana SAP con el título indicado.

    Hardcoded:
        - _SLEEP_LARGO = 0.4: espera tras SetForegroundWindow (TIMING)
    """
    hwnd = _encontrar_hwnd(titulo_contiene)
    if not hwnd:
        raise RuntimeError(
            f"Ventana SAP no encontrada"
            + (f" con título '{titulo_contiene}'" if titulo_contiene else "")
        )
    placement = win32gui.GetWindowPlacement(hwnd)
    if placement[1] == win32con.SW_SHOWMINIMIZED:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
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
    time.sleep(_SLEEP_LARGO)
    return hwnd


def esperar_titulo(titulo_contiene, timeout=15):
    """Espera hasta que aparezca una ventana SAP con el título esperado.

    Sondea cada 0.3 segundos hasta agotar el timeout.

    Args:
        titulo_contiene (str): Fragmento de título a esperar.
        timeout (int): Segundos máximos de espera. Default: 15.

    Returns:
        bool: True cuando se encontró la ventana.

    Raises:
        RuntimeError: Si el timeout se agota sin encontrar la pantalla.

    Hardcoded:
        - 0.3: intervalo de sondeo en segundos (TIMING)
    """
    fin = time.time() + timeout
    while time.time() < fin:
        if _encontrar_hwnd(titulo_contiene):
            return True
        time.sleep(_SLEEP_MEDIO)
    raise RuntimeError(f"Pantalla '{titulo_contiene}' no aparecio en {timeout}s")


def verificar_pantalla(titulo_esperado: str, paso: str) -> str:
    """Verifica inmediatamente que la pantalla activa sea la esperada.

    Comparación sin tilde y case-insensitive. Lanza RuntimeError con
    mensaje descriptivo si la pantalla no coincide.

    Args:
        titulo_esperado (str): Título (o fragmento) de la pantalla esperada.
        paso (str): Identificador del paso para el log (ej. "FB60-Inicio").

    Returns:
        str: Título completo de la ventana activa.

    Raises:
        RuntimeError: Si la pantalla activa no contiene el título esperado.
    """
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
    """Retorna el texto del título de la ventana SAP activa.

    Returns:
        str: Título de la ventana, o cadena vacía si SAP no está abierto.
    """
    hwnd = _encontrar_hwnd()
    return win32gui.GetWindowText(hwnd) if hwnd else ""


def rect():
    """Retorna las coordenadas del área cliente de la ventana SAP.

    Returns:
        tuple[int,int,int,int]: (left, top, right, bottom) en pixels de pantalla.
    """
    hwnd = _encontrar_hwnd()
    if not hwnd:
        return (0, 0, 0, 0)
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    cr = win32gui.GetClientRect(hwnd)
    return left, top, left + cr[2], top + cr[3]


def pos_en_ventana(rel_x, rel_y):
    """Convierte coordenadas relativas al área cliente SAP a coordenadas de pantalla.

    Args:
        rel_x (int): Posición X relativa al borde izquierdo del área cliente.
        rel_y (int): Posición Y relativa al borde superior del área cliente.

    Returns:
        tuple[int,int]: (x, y) en coordenadas absolutas de pantalla.
    """
    left, top, _, _ = rect()
    return left + rel_x, top + rel_y


def posicionar_ventana(x=None, y=None, ancho=None, alto=None):
    """Mueve y redimensiona la ventana SAP a las dimensiones del .env.

    Si la ventana está maximizada o minimizada, la restaura antes de mover.
    Sin argumentos usa SAP_WIN_X/Y/ANCHO/ALTO del .env.

    Args:
        x (int, optional): Posición X. Default: SAP_WIN_X del .env.
        y (int, optional): Posición Y. Default: SAP_WIN_Y del .env.
        ancho (int, optional): Ancho en pixels. Default: SAP_WIN_ANCHO del .env.
        alto (int, optional): Alto en pixels. Default: SAP_WIN_ALTO del .env.

    Returns:
        None

    Raises:
        RuntimeError: Si no hay ventana SAP abierta.

    Hardcoded:
        - "0", "1024", "768": valores por defecto de posición/tamaño (CONFIG)
    """
    import os, ctypes
    x     = int(os.getenv("SAP_WIN_X",     "0"))    if x     is None else x
    y     = int(os.getenv("SAP_WIN_Y",     "0"))    if y     is None else y
    _ancho_cfg = int(os.getenv("SAP_WIN_ANCHO", "1024")) if ancho is None else ancho
    ancho = ctypes.windll.user32.GetSystemMetrics(0) if _ancho_cfg == 0 else _ancho_cfg
    _alto_cfg = int(os.getenv("SAP_WIN_ALTO",  "768"))  if alto  is None else alto
    alto  = ctypes.windll.user32.GetSystemMetrics(1) if _alto_cfg == 0 else _alto_cfg
    hwnd = _encontrar_hwnd()
    if not hwnd:
        raise RuntimeError("Ventana SAP no encontrada")
    placement = win32gui.GetWindowPlacement(hwnd)
    if placement[1] in (win32con.SW_SHOWMINIMIZED, win32con.SW_SHOWMAXIMIZED):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(_SLEEP_MEDIO)
    try:
        win32gui.MoveWindow(hwnd, x, y, ancho, alto, True)
    except Exception:
        pass   # modal dialog activo o restricción del proceso — ignorar
    time.sleep(_SLEEP_LARGO)


def mover_a_origen():
    """Mueve la ventana SAP a (0,0) sin cambiar su tamaño.

    Necesario antes de clics con coordenadas calibradas en posición (0,0).

    Returns:
        None
    """
    hwnd = _encontrar_hwnd()
    if not hwnd:
        return
    placement = win32gui.GetWindowPlacement(hwnd)
    if placement[1] == win32con.SW_SHOWMINIMIZED:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    r = win32gui.GetWindowRect(hwnd)
    w, h = r[2] - r[0], r[3] - r[1]
    win32gui.MoveWindow(hwnd, 0, 0, w, h, True)
    time.sleep(_SLEEP_MEDIO)


def info_ventana():
    """Imprime en consola las coordenadas y tamaño de la ventana SAP.

    Útil para diagnóstico y calibración de coordenadas relativas.

    Returns:
        None
    """
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

def escribir(texto, delay=_INTERVAL_WRITE):
    """Escribe texto carácter a carácter via pyautogui.write.

    Args:
        texto (str | int): Texto a escribir en el campo activo.
        delay (float): Pausa entre caracteres en segundos. Default: 0.05.

    Returns:
        None
    """
    pyautogui.write(str(texto), interval=delay)


def tecla(key):
    """Presiona y suelta una tecla SAP via pynput.

    Args:
        key (str): Nombre de tecla del _KEY_MAP (ej. 'enter', 'down', 'f8').

    Returns:
        None
    """
    _press(key)


def combo(*keys):
    """Envía una combinación de teclas via pyautogui.hotkey.

    Nota: Ctrl+/ debe ir por aquí (pyautogui) — pynput no activa
    la barra de comandos SAP con esa combinación.

    Args:
        *keys (str): Teclas en orden (ej. 'ctrl', '/').

    Returns:
        None
    """
    pyautogui.hotkey(*keys)


def tab(n=1):
    """Envía n pulsaciones de Tab via pynput.

    Args:
        n (int): Número de tabs a enviar. Default: 1.

    Returns:
        None
    """
    tab_pynput(n)


def tab_pynput(n=1):
    """Envía n tabs via pynput con pausa entre cada uno.

    Más fiable que pyautogui.press('tab') cuando SAP no recibe el Tab normal.

    Args:
        n (int): Número de tabs. Default: 1.

    Returns:
        None

    Hardcoded:
        - _SLEEP_CORTO = 0.05: pausa dentro de press/release (TIMING)
        - _SLEEP_CORTO = 0.1: pausa entre tabs (TIMING)
    """
    for _ in range(n):
        _kb.press(Key.tab)
        time.sleep(_SLEEP_CORTO)
        _kb.release(Key.tab)
        time.sleep(_SLEEP_CORTO)


def shift_tab(n=1):
    """Envía n pulsaciones de Shift+Tab (retrocede entre campos SAP).

    Args:
        n (int): Número de Shift+Tab. Default: 1.

    Returns:
        None
    """
    for _ in range(n):
        with _kb.pressed(Key.shift):
            _kb.press(Key.tab)
            _kb.release(Key.tab)
        time.sleep(_SLEEP_CORTO)


def copiar():
    """Copia el campo activo al portapapeles con Ctrl+A (seleccionar) + Ctrl+C.

    Returns:
        None
    """
    with _kb.pressed(Key.ctrl):
        _kb.press('a'); _kb.release('a')
    time.sleep(_SLEEP_CORTO)
    with _kb.pressed(Key.ctrl):
        _kb.press('c'); _kb.release('c')
    time.sleep(_SLEEP_MEDIO)


def leer_portapapeles() -> str:
    """Lee el contenido actual del portapapeles.

    Returns:
        str: Texto del portapapeles, o cadena vacía si falla.
    """
    try:
        import pyperclip
        return (pyperclip.paste() or "").strip()
    except Exception:
        return ""


def pegar_como_texto():
    """Lee el portapapeles y lo tipea en el campo activo (Ctrl+A + write).

    Returns:
        None
    """
    texto = leer_portapapeles()
    if texto:
        with _kb.pressed(Key.ctrl):
            _kb.press('a'); _kb.release('a')
        time.sleep(_SLEEP_CORTO)
        pyautogui.write(texto)


def pegar_fecha():
    """Lee el portapapeles y tipea la fecha incluyendo puntos del separador.

    SAP salta los puntos de la máscara automáticamente al tipear,
    por lo que se escribe el texto completo (DD.MM.YYYY) carácter a carácter.

    Returns:
        None

    Hardcoded:
        - 0.05: intervalo entre caracteres (TIMING)
    """
    texto = leer_portapapeles()
    if texto:
        with _kb.pressed(Key.ctrl):
            _kb.press('a'); _kb.release('a')
        time.sleep(_SLEEP_CORTO)
        pyautogui.write(texto, interval=_INTERVAL_WRITE)


def enter():
    """Envía Enter via pynput.

    Returns:
        None
    """
    _press('enter')


def limpiar():
    """Selecciona y borra el contenido del campo activo.

    Usa Home + Shift+End para seleccionar dentro del campo.
    No usa Shift+Home porque en SAP salta al primer campo del formulario.

    Returns:
        None
    """
    _press('home')
    with _kb.pressed(Key.shift):
        _kb.press(Key.end); _kb.release(Key.end)
    time.sleep(_SLEEP_CORTO)
    _press('delete')


def campo(texto):
    """Limpia el campo activo y escribe el texto.

    Args:
        texto (str): Texto a escribir tras limpiar.

    Returns:
        None
    """
    limpiar()
    escribir(texto)


def campo_ctrlA(texto):
    """Limpia con Ctrl+A + Delete y escribe. Para campos donde Home no funciona.

    Usar en campos del formulario ZFIEC015 donde Home navega fuera del campo.

    Args:
        texto (str): Texto a escribir tras limpiar.

    Returns:
        None
    """
    with _kb.pressed(Key.ctrl):
        _kb.press('a'); _kb.release('a')
    time.sleep(_SLEEP_CORTO)
    _press('delete')
    time.sleep(_SLEEP_CORTO)
    escribir(texto)


def campo_fecha(texto):
    """Limpia y escribe en un campo de fecha SAP.

    Alias de campo() — los campos de fecha funcionan igual que los de texto.

    Args:
        texto (str): Fecha a escribir (ej. "01.06.2026").

    Returns:
        None
    """
    limpiar()
    escribir(texto)


def f8():
    """Envía F8 (Ejecutar en SAP).

    Returns:
        None
    """
    _press('f8')


def salir_tabla():
    """Sale de la tabla de posiciones FB60 con 4x Ctrl+Shift+Tab.

    Sube el foco desde dentro de la tabla hasta el encabezado de cabecera.
    Desde cabecera funciona Ctrl+Shift+AvPág para cambiar de pestaña.

    Returns:
        None

    Hardcoded:
        - 4: número de Ctrl+Shift+Tab necesarios para salir (NÚMERO MÁGICO calibrado)
        - 0.1 / 0.4: timings internos (TIMING)
    """
    for _ in range(4):
        with _kb.pressed(Key.ctrl):
            with _kb.pressed(Key.shift):
                time.sleep(_SLEEP_CORTO)
                _kb.press(Key.tab)
                _kb.release(Key.tab)
        time.sleep(_SLEEP_LARGO)

def siguiente_pestana():
    """Avanza a la siguiente pestaña en FB60 con Ctrl+Shift+AvPág (pynput)."""
    with _kb.pressed(Key.ctrl):
        with _kb.pressed(Key.shift):
            time.sleep(_SLEEP_CORTO)
            _kb.press(Key.page_down)
            _kb.release(Key.page_down)
    time.sleep(_SLEEP_PESTANA)


def pestana_anterior():
    """Retrocede a la pestaña anterior en FB60 con Ctrl+Shift+RePág (pynput)."""
    with _kb.pressed(Key.ctrl):
        with _kb.pressed(Key.shift):
            time.sleep(_SLEEP_CORTO)
            _kb.press(Key.page_up)
            _kb.release(Key.page_up)
    time.sleep(_SLEEP_PESTANA)

def ctrl_s():
    """Envía Ctrl+S (Guardar/Contabilizar en SAP) via pynput VK directo.

    Returns:
        None
    """
    from pynput.keyboard import KeyCode
    _s_vk = KeyCode.from_vk(0x53)
    with _kb.pressed(Key.ctrl):
        _kb.press(_s_vk); _kb.release(_s_vk)


def escape():
    """Envía Escape via pynput.

    Returns:
        None
    """
    _press('escape')


def f3():
    """Envía F3 (Retroceder en SAP) via pynput.

    Returns:
        None
    """
    _press('f3')

def f12():
    """Envía F12 (Cancelar/Abandonar en SAP) via pynput.

    Returns:
        None
    """
    _press('f12')


# ─────────────────────────────────────────────────────────────
# Mouse
# ─────────────────────────────────────────────────────────────

def click_en(rel_x, rel_y, boton='left'):
    """Clic en coordenada relativa al área cliente de la ventana SAP.

    Args:
        rel_x (int): Posición X relativa al borde izquierdo del cliente.
        rel_y (int): Posición Y relativa al borde superior del cliente.
        boton (str): 'left', 'right' o 'middle'. Default: 'left'.

    Returns:
        None
    """
    x, y = pos_en_ventana(rel_x, rel_y)
    pyautogui.click(x, y, button=boton)
    time.sleep(_SLEEP_CORTO)


def doble_click_en(rel_x, rel_y):
    """Doble clic en coordenada relativa al área cliente SAP.

    Args:
        rel_x (int): Posición X relativa.
        rel_y (int): Posición Y relativa.

    Returns:
        None
    """
    x, y = pos_en_ventana(rel_x, rel_y)
    pyautogui.doubleClick(x, y)
    time.sleep(_SLEEP_CORTO)


def click_win32(rel_x, rel_y):
    """Clic vía win32api.mouse_event (nivel bajo, alternativa a pyautogui).

    Args:
        rel_x (int): Posición X relativa al área cliente SAP.
        rel_y (int): Posición Y relativa al área cliente SAP.

    Returns:
        None
    """
    x, y = pos_en_ventana(rel_x, rel_y)
    win32api.SetCursorPos((x, y))
    time.sleep(_SLEEP_CORTO)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
    time.sleep(_SLEEP_CORTO)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
    time.sleep(_SLEEP_CORTO)


def click_postmsg(grid_rel_x, grid_rel_y, grid_clase="SAPALVGrid"):
    """Envía WM_LBUTTONDOWN directamente al control SAPALVGrid hijo.

    Alternativa a clics pyautogui cuando el control de grilla no recibe
    eventos de ratón de la manera estándar.

    Args:
        grid_rel_x (int): Coordenada X dentro del control SAPALVGrid.
        grid_rel_y (int): Coordenada Y dentro del control SAPALVGrid.
        grid_clase (str): Clase Win32 del control. Default: "SAPALVGrid".

    Returns:
        None

    Raises:
        RuntimeError: Si no se encuentra la ventana SAP o el control de grilla.

    Hardcoded:
        - "SAPALVGrid": clase del control de grilla SAP (STRING)
    """
    parent = _encontrar_hwnd()
    if not parent:
        raise RuntimeError("Ventana SAP no encontrada")

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
    time.sleep(_SLEEP_CORTO)
    win32api.PostMessage(grid_hwnd, win32con.WM_LBUTTONUP, 0, lparam)
    time.sleep(_SLEEP_MEDIO)


# ─────────────────────────────────────────────────────────────
# Navegación de transacciones
# ─────────────────────────────────────────────────────────────

def ir_a(tcode):
    """Navega a un código de transacción SAP usando la barra de comandos.

    Usa Ctrl+/ (pyautogui) para abrir la barra, limpia con Ctrl+A+Delete,
    escribe el tcode y presiona Enter. Después posiciona la ventana en (0,0).

    Args:
        tcode (str): Código de transacción SAP (ej. "ZFIEC015", "FB60").
                     Si no comienza con '/' se antepone '/n' para forzar navegación.

    Returns:
        None

    Hardcoded:
        - '/n': prefijo de navegación SAP (STRING SAP)
        - 1.5: espera tras Enter del tcode (TIMING)
    """
    activar()
    combo('ctrl', '/')   # pyautogui.hotkey — Ctrl+/ activa barra de comandos SAP
    time.sleep(_SLEEP_MEDIO)
    with _kb.pressed(Key.ctrl):
        _kb.press('a'); _kb.release('a')
    time.sleep(_SLEEP_CORTO)
    _press('delete')
    time.sleep(_SLEEP_CORTO)
    cmd = tcode if tcode.startswith('/') else f'/n{tcode}'
    escribir(cmd)
    enter()
    time.sleep(_SLEEP_TCODE)
    posicionar_ventana()


# ─────────────────────────────────────────────────────────────
# Popups
# ─────────────────────────────────────────────────────────────

def confirmar_popup_si():
    """Confirma un popup SAP presionando Enter (botón Sí / OK por defecto).

    Returns:
        None
    """
    time.sleep(_SLEEP_LARGO)
    enter()
    time.sleep(_SLEEP_MEDIO)


def cancelar_popup():
    """Cancela un popup SAP presionando Escape.

    Returns:
        None
    """
    time.sleep(_SLEEP_MEDIO)
    escape()
    time.sleep(_SLEEP_MEDIO)


def cerrar_sap():
    """Cierra SAP completamente desde cualquier pantalla.

    Intento 1: SAP Scripting — cierra la ventana y acepta popup de confirmación.
    Intento 2: SC_CLOSE + hilo separado que presiona Tab+Enter en el popup.

    Returns:
        None

    Hardcoded:
        - "wnd[1]/usr/btnSPOP-VAROPTION1": ID botón Sí en popup SAP (STRING SAP)
        - "wnd[1]/usr/btn[0]": ID alternativo botón Sí (STRING SAP)
        - "wnd[1]/tbar[0]/btn[0]": ID alternativo botón OK (STRING SAP)
        - 1.2, 1.5, 0.3, 0.25, 5: tiempos de espera (TIMING)
    """
    if not _encontrar_hwnd():
        return
    try:
        import win32com.client

        # ── Intento 1: SAP Scripting ──
        try:
            app     = win32com.client.GetObject("SAPGUI").GetScriptingEngine
            session = app.Children(0).Children(0)
            _       = session.Type

            session.findById("wnd[0]").close()
            time.sleep(_SLEEP_TCODE)

            for btn_id in (
                "wnd[1]/usr/btnSPOP-VAROPTION1",
                "wnd[1]/usr/btn[0]",
                "wnd[1]/tbar[0]/btn[0]",
            ):
                try:
                    session.findById(btn_id).press()
                    time.sleep(_SLEEP_TCODE)
                    if not _encontrar_hwnd():
                        return
                    break
                except Exception:
                    continue
        except Exception:
            pass

        if not _encontrar_hwnd():
            return

        # ── Intento 2: /nend → popup "Salir del sistema" → Tab+Enter ──
        # "No" es el botón por defecto — Tab mueve a "Sí", Enter confirma
        import ctypes, win32process
        from pynput.keyboard import Controller as _KbCtrl, Key as _Key
        _kbd2 = _KbCtrl()

        ir_a("/nend")
        time.sleep(_SLEEP_NEND)
        hwnd_popup = win32gui.FindWindow(None, "Salir del sistema")
        if hwnd_popup:
            tid_sap = win32process.GetWindowThreadProcessId(hwnd_popup)[0]
            tid_yo  = win32api.GetCurrentThreadId()
            ctypes.windll.user32.AttachThreadInput(tid_yo, tid_sap, True)
            win32gui.BringWindowToTop(hwnd_popup)
            win32gui.SetForegroundWindow(hwnd_popup)
            ctypes.windll.user32.AttachThreadInput(tid_yo, tid_sap, False)
            time.sleep(_SLEEP_MEDIO)
            _kbd2.press(_Key.tab);   _kbd2.release(_Key.tab)
            time.sleep(_SLEEP_CORTO)
            _kbd2.press(_Key.enter); _kbd2.release(_Key.enter)

    except Exception:
        pass
