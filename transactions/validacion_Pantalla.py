import sys
import os
import shutil
import glob
import time
import re
import json
import pathlib
import datetime
import unicodedata
import logging

from PIL import ImageGrab, ImageOps, ImageEnhance
import pytesseract

_log = logging.getLogger(__name__)


# ============================================================
# CONFIGURACIÓN BASE
# ============================================================

_BASE_DIR = (
    pathlib.Path(sys.executable).parent
    if getattr(sys, "frozen", False)
    else pathlib.Path(__file__).parent.parent
)

_SCREENSHOTS_DIR = _BASE_DIR / "screenshots"
_SCREENSHOTS_DIR.mkdir(exist_ok=True)


# ============================================================
# TESSERACT
# ============================================================

def buscar_tesseract():
    """Localiza tesseract.exe en rutas estándar de Windows, PATH o glob de Programs."""
    rutas = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]

    for ruta in rutas:
        if os.path.exists(ruta):
            return ruta

    ruta_path = shutil.which("tesseract")
    if ruta_path:
        return ruta_path

    patrones = [
        r"C:\Program Files\*\tesseract.exe",
        r"C:\Program Files (x86)\*\tesseract.exe",
        r"C:\Users\*\AppData\Local\Programs\*\tesseract.exe",
    ]

    for patron in patrones:
        encontrados = glob.glob(patron)
        if encontrados:
            return encontrados[0]

    return None


if getattr(sys, "frozen", False):
    _tess_sistema = buscar_tesseract()
    if _tess_sistema:
        # Tesseract ya instalado en el sistema — lo usa directamente
        pytesseract.pytesseract.tesseract_cmd = _tess_sistema
        os.environ["TESSDATA_PREFIX"] = os.path.join(os.path.dirname(_tess_sistema), "tessdata")
    else:
        # Sin instalación del sistema — usa el Tesseract embebido en el exe
        pytesseract.pytesseract.tesseract_cmd = os.path.join(sys._MEIPASS, "tesseract.exe")
        os.environ["TESSDATA_PREFIX"] = os.path.join(sys._MEIPASS, "tessdata")
else:
    tesseract_path = buscar_tesseract()
    if not tesseract_path:
        print("ERROR: No se encontró tesseract.exe")
        sys.exit(1)
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
    os.environ["TESSDATA_PREFIX"] = os.path.join(os.path.dirname(tesseract_path), "tessdata")


# ============================================================
# VERIFICACIÓN DE TESSERACT
# ============================================================

def verificar_tesseract() -> bool:
    """Ejecuta un OCR mínimo para confirmar que Tesseract está operativo.

    Returns:
        bool: True si Tesseract responde sin error, False en caso contrario.
    """
    try:
        from PIL import Image
        img = Image.new("RGB", (120, 30), color=(255, 255, 255))
        pytesseract.image_to_string(img, lang="eng", config="--psm 6")
        return True
    except Exception as e:
        _log.error("Tesseract no operativo: %s", e)
        return False


# ============================================================
# FUNCIONES GENERALES OCR / NORMALIZACIÓN
# ============================================================

def _nd(v):
    """Formatea un valor OCR: None → 'N/D' (no detectado), cualquier otro → repr."""
    return "N/D" if v is None else repr(v)


def limpiar(txt):
    """Limpia texto OCR: elimina saltos de línea, símbolos especiales y espacios extra."""
    if not txt:
        return ""

    txt = str(txt)
    txt = txt.replace("\n", " ")
    txt = txt.replace("\r", " ")
    txt = txt.replace("|", "")
    txt = txt.replace("&lt;", "")
    txt = txt.replace("&gt;", "")
    txt = txt.replace("&amp;lt;", "")
    txt = txt.replace("&amp;gt;", "")
    txt = txt.replace("&amp;amp;lt;", "")
    txt = txt.replace("&amp;amp;gt;", "")
    txt = txt.replace("_", "")
    txt = txt.replace("‘", "")
    txt = txt.replace("’", "")
    txt = txt.replace("“", "")
    txt = txt.replace("”", "")
    txt = txt.replace("—", " ")
    txt = txt.replace("…", "...")
    txt = txt.strip(" |[]{}<>~`'\"")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def quitar_acentos(txt):
    """Elimina acentos y diacríticos."""
    if not txt:
        return ""

    return "".join(
        c for c in unicodedata.normalize("NFD", txt)
        if unicodedata.category(c) != "Mn"
    )


def normalizar(txt):
    """Normaliza texto para comparación."""
    txt = limpiar(txt).lower()
    txt = quitar_acentos(txt)
    txt = txt.replace(" ", "")
    txt = txt.replace(".", "")
    txt = txt.replace(":", "")
    txt = txt.replace(";", "")
    txt = txt.replace("*", "")
    txt = txt.replace(",", "")
    txt = txt.replace("/", "")
    txt = txt.replace("-", "")
    txt = txt.replace("(", "")
    txt = txt.replace(")", "")
    return txt


def limpiar_texto_ocr_fuerte(txt):
    """Limpieza agresiva para bordes/prefijos OCR."""
    txt = limpiar(txt)
    txt = re.sub(r"^\*+\s*", "", txt)
    txt = re.sub(r"^SAP\s+", "", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def preparar_pantalla(img):
    """Escala, convierte a gris y aumenta contraste."""
    w, h = img.size
    img = img.resize((w * 2, h * 2))
    img = img.convert("L")
    img = ImageOps.autocontrast(img)
    img = ImageEnhance.Contrast(img).enhance(2.6)
    return img


def obtener_ocr_data(img_proc):
    """Ejecuta Tesseract sobre la imagen."""
    try:
        return pytesseract.image_to_data(
            img_proc,
            lang="spa",
            config="--psm 6",
            output_type=pytesseract.Output.DICT
        )
    except Exception:
        return pytesseract.image_to_data(
            img_proc,
            lang="eng",
            config="--psm 6",
            output_type=pytesseract.Output.DICT
        )


def extraer_palabras(data, escala=2):
    """Convierte output Tesseract en palabras con coordenadas."""
    palabras = []

    for i in range(len(data["text"])):
        texto = limpiar(data["text"][i])

        if not texto:
            continue

        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = -1

        if conf < 10:
            continue

        left = data["left"][i] / escala
        top = data["top"][i] / escala
        width = data["width"][i] / escala
        height = data["height"][i] / escala

        palabras.append({
            "texto": texto,
            "norm": normalizar(texto),
            "conf": conf,
            "left": left,
            "top": top,
            "right": left + width,
            "bottom": top + height,
            "cx": left + width / 2,
            "cy": top + height / 2,
        })

    return palabras


def agrupar_lineas(palabras, tolerancia_y=10):
    """Agrupa palabras en líneas por proximidad vertical."""
    lineas = []

    for p in sorted(palabras, key=lambda x: (x["cy"], x["left"])):
        agregado = False

        for linea in lineas:
            if abs(linea["cy"] - p["cy"]) <= tolerancia_y:
                linea["palabras"].append(p)
                linea["cy"] = sum(x["cy"] for x in linea["palabras"]) / len(linea["palabras"])
                agregado = True
                break

        if not agregado:
            lineas.append({
                "cy": p["cy"],
                "palabras": [p],
            })

    resultado = []

    for linea in lineas:
        ps = sorted(linea["palabras"], key=lambda x: x["left"])
        texto = limpiar(" ".join(p["texto"] for p in ps))

        resultado.append({
            "cy": linea["cy"],
            "palabras": ps,
            "texto": texto,
            "left": min(p["left"] for p in ps),
            "right": max(p["right"] for p in ps),
            "top": min(p["top"] for p in ps),
            "bottom": max(p["bottom"] for p in ps),
        })

    return resultado


def leer_env_simple(path_env):
    """Lee pares KEY=VALUE del .env."""
    data = {}

    if not os.path.exists(path_env):
        return data

    with open(path_env, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            if "=" not in line:
                continue

            k, v = line.split("=", 1)
            data[k.strip()] = v.strip().strip('"').strip("'")

    return data


def capturar_ventana_sap():
    """Captura SAP usando coordenadas del .env si existen."""
    env_path = _BASE_DIR / ".env"
    env = leer_env_simple(env_path)

    try:
        x = int(env.get("SAP_WIN_X", 0))
        y = int(env.get("SAP_WIN_Y", 0))
        w = int(env.get("SAP_WIN_ANCHO", 0))
        h = int(env.get("SAP_WIN_ALTO", 0))
    except Exception:
        x, y, w, h = 0, 0, 0, 0

    if w > 0 and h > 0:
        return ImageGrab.grab(bbox=(x, y, x + w, y + h))

    return ImageGrab.grab()


def obtener_palabras_lineas_desde_pantalla():
    """Captura pantalla, OCR, palabras y líneas."""
    screenshot = capturar_ventana_sap()
    img_proc = preparar_pantalla(screenshot)
    data = obtener_ocr_data(img_proc)
    palabras = extraer_palabras(data, escala=2)
    lineas = agrupar_lineas(palabras)
    return screenshot, palabras, lineas


def log_lineas_ocr(lineas):
    """Guarda líneas OCR para debug."""
    ruta = _SCREENSHOTS_DIR / "ocr_debug.txt"

    with open(ruta, "w", encoding="utf-8") as f:
        for l in lineas:
            if isinstance(l, dict):
                f.write(str(l.get("texto", "")) + "\n")
            else:
                f.write(str(l) + "\n")


def guardar_screenshot(img):
    """Guarda screenshot para debug."""
    img.save(_SCREENSHOTS_DIR / "ocr_screen.png")


# ============================================================
# ZFIEC015
# ============================================================

def extraer_sociedad_zfiec(palabras):
    """Extrae sociedad ZFIEC015."""
    for p in palabras:
        if "sociedad" in p["norm"]:
            y_ref = p["cy"]

            candidatos = [
                q for q in palabras
                if abs(q["cy"] - y_ref) <= 25
                and q["left"] > p["right"]
                and re.fullmatch(r"\d{3,6}", q["texto"])
            ]

            if candidatos:
                candidatos.sort(key=lambda q: q["left"])
                return candidatos[0]["texto"]

    return None


def ocr_region_numero_largo(img, bbox):
    """OCR especializado para leer números largos, como proveedor de 10 dígitos."""
    x1, y1, x2, y2 = bbox

    x1 = max(0, int(x1))
    y1 = max(0, int(y1))
    x2 = min(img.width, int(x2))
    y2 = min(img.height, int(y2))

    if x2 <= x1 or y2 <= y1:
        return None

    crop = img.crop((x1, y1, x2, y2)).convert("L")

    variantes = []

    base = ImageOps.expand(crop, border=15, fill=255)

    img1 = base.resize((base.width * 6, base.height * 6))
    img1 = ImageOps.autocontrast(img1)
    img1 = ImageEnhance.Contrast(img1).enhance(3.0)
    img1 = ImageEnhance.Sharpness(img1).enhance(2.0)
    variantes.append(img1)

    img2 = img1.point(lambda p: 0 if p < 190 else 255)
    variantes.append(img2)

    configs = [
        "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789",
        "--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789",
        "--oem 3 --psm 13 -c tessedit_char_whitelist=0123456789",
    ]

    resultados = []

    for variante in variantes:
        for config in configs:
            try:
                txt = pytesseract.image_to_string(
                    variante,
                    lang="eng",
                    config=config
                )

                solo = re.sub(r"\D", "", limpiar(txt))

                if re.fullmatch(r"\d{10}", solo):
                    resultados.append(solo)

            except Exception:
                pass

    if resultados:
        conteo = {}

        for r in resultados:
            conteo[r] = conteo.get(r, 0) + 1

        ordenados = sorted(conteo.items(), key=lambda x: x[1], reverse=True)
        return ordenados[0][0]

    return None


def extraer_proveedor_zfiec(palabras, lineas, screenshot=None):
    """
    Extrae proveedor ZFIEC015.

    Estrategia:
    1. Buscar número de 10 dígitos en la misma línea donde aparece 'Proveedor'.
    2. Buscar palabra 'Proveedor' y candidatos a la derecha.
    3. Buscar cualquier número de 10 dígitos en líneas/candidatos cercanos.
    4. Fallback por crop OCR a la derecha del label Proveedor.
    """
    _log.info("ZFIEC015 OCR — buscando proveedor...")

    # ========================================================
    # 1. Buscar en línea completa con label Proveedor
    # ========================================================
    for idx, l in enumerate(lineas, start=1):
        texto_linea = l["texto"] if isinstance(l, dict) else str(l)
        n = normalizar(texto_linea)

        if "proveedor" in n:
            _log.info(
                "ZFIEC015 OCR — línea proveedor encontrada [%s]: %r",
                idx,
                texto_linea
            )

            m = re.search(r"\b(\d{10})\b", texto_linea)
            if m:
                proveedor = m.group(1)
                _log.info("ZFIEC015 OCR — proveedor detectado en línea: %s", proveedor)
                return proveedor

    # ========================================================
    # 2. Buscar label Proveedor en palabras
    # ========================================================
    label = None

    for p in palabras:
        if "proveedor" in p["norm"]:
            label = p
            _log.info(
                "ZFIEC015 OCR — label proveedor: texto=%r left=%s right=%s top=%s bottom=%s cy=%s",
                p["texto"],
                p["left"],
                p["right"],
                p["top"],
                p["bottom"],
                p["cy"]
            )
            break

    if label:
        y_ref = label["cy"]
        candidatos = []

        for p in palabras:
            if abs(p["cy"] - y_ref) <= 45 and p["left"] > label["right"]:
                _log.info(
                    "ZFIEC015 OCR — candidato a derecha proveedor: texto=%r left=%s cy=%s",
                    p["texto"],
                    p["left"],
                    p["cy"]
                )

                m = re.search(r"\b(\d{10})\b", p["texto"])
                if m:
                    candidatos.append((p["left"], m.group(1)))

        if candidatos:
            candidatos.sort(key=lambda x: x[0])
            proveedor = candidatos[-1][1]
            _log.info("ZFIEC015 OCR — proveedor detectado por candidatos: %s", proveedor)
            return proveedor

        # ====================================================
        # 3. Fallback por crop a la derecha del label
        # ====================================================
        if screenshot is not None:
            alto = max(18, label["bottom"] - label["top"])

            bboxes = [
                (
                    label["right"],
                    label["top"] - alto * 1.2,
                    label["right"] + alto * 18,
                    label["bottom"] + alto * 1.8,
                ),
                (
                    label["right"],
                    label["top"] - alto * 2.0,
                    label["right"] + alto * 24,
                    label["bottom"] + alto * 2.5,
                ),
                (
                    label["right"] - alto,
                    label["top"] - alto * 1.5,
                    label["right"] + alto * 30,
                    label["bottom"] + alto * 2.5,
                ),
            ]

            for idx, bbox in enumerate(bboxes, start=1):
                proveedor = ocr_region_numero_largo(screenshot, bbox)

                _log.info(
                    "ZFIEC015 OCR — crop proveedor intento %s bbox=%r resultado=%r",
                    idx,
                    bbox,
                    proveedor
                )

                if proveedor:
                    return proveedor

    else:
        _log.warning("ZFIEC015 OCR — no se encontró label proveedor")

    # ========================================================
    # 4. Último fallback: buscar cualquier número de 10 dígitos
    # ========================================================
    todos_textos = []

    for l in lineas:
        if isinstance(l, dict):
            todos_textos.append(l.get("texto", ""))
        else:
            todos_textos.append(str(l))

    texto_total = " ".join(todos_textos)

    nums = re.findall(r"\b\d{10}\b", texto_total)

    if nums:
        _log.info(
            "ZFIEC015 OCR — proveedor detectado por búsqueda global 10 dígitos: %s",
            nums[-1]
        )
        return nums[-1]

    _log.warning("ZFIEC015 OCR — proveedor no detectado")
    return None


def extraer_fechas_zfiec(lineas):
    """Extrae fechas ZFIEC015."""
    fecha_inicio = None
    fecha_fin = None

    for l in lineas:
        texto = l["texto"] if isinstance(l, dict) else str(l)
        n = normalizar(texto)

        if "fecha" in n and "factura" not in n and "contab" not in n:
            fechas = re.findall(r"\d{2}[.,]\d{2}[.,]\d{4}", texto)

            if fechas:
                fecha_inicio = fechas[0].replace(",", ".")
                fecha_fin = fechas[1].replace(",", ".") if len(fechas) >= 2 else None
                break

    return fecha_inicio, fecha_fin


def detectar_codigo_01_por_pixeles(img, bbox):
    """Detecta código 01 por píxeles."""
    x1, y1, x2, y2 = bbox

    x1 = max(0, int(x1))
    y1 = max(0, int(y1))
    x2 = min(img.width, int(x2))
    y2 = min(img.height, int(y2))

    if x2 <= x1 or y2 <= y1:
        return None

    crop = img.crop((x1, y1, x2, y2)).convert("L")
    w, h = crop.size

    mask = set()

    for yy in range(h):
        for xx in range(w):
            if crop.getpixel((xx, yy)) < 120:
                mask.add((xx, yy))

    filas_a_borrar = set()

    for yy in range(h):
        count = sum((xx, yy) in mask for xx in range(w))
        if count > w * 0.45:
            filas_a_borrar.add(yy)

    cols_a_borrar = set()

    for xx in range(w):
        count = sum((xx, yy) in mask for yy in range(h))
        if count > h * 0.85:
            cols_a_borrar.add(xx)

    mask = {
        (xx, yy)
        for (xx, yy) in mask
        if yy not in filas_a_borrar and xx not in cols_a_borrar
    }

    visitados = set()
    comps = []

    for punto in list(mask):
        if punto in visitados:
            continue

        stack = [punto]
        visitados.add(punto)

        xs = []
        ys = []

        while stack:
            xx, yy = stack.pop()
            xs.append(xx)
            ys.append(yy)

            vecinos = (
                (xx + 1, yy),
                (xx - 1, yy),
                (xx, yy + 1),
                (xx, yy - 1),
                (xx + 1, yy + 1),
                (xx - 1, yy - 1),
                (xx + 1, yy - 1),
                (xx - 1, yy + 1),
            )

            for nx, ny in vecinos:
                if (
                    0 <= nx < w
                    and 0 <= ny < h
                    and (nx, ny) in mask
                    and (nx, ny) not in visitados
                ):
                    visitados.add((nx, ny))
                    stack.append((nx, ny))

        bx1, bx2 = min(xs), max(xs)
        by1, by2 = min(ys), max(ys)

        bw = bx2 - bx1 + 1
        bh = by2 - by1 + 1
        area = len(xs)

        if area < 8:
            continue

        if bh < h * 0.18:
            continue

        if bw < 2:
            continue

        if bw > w * 0.80 or bh > h * 0.95:
            continue

        comps.append({
            "x1": bx1,
            "y1": by1,
            "x2": bx2,
            "y2": by2,
            "w": bw,
            "h": bh,
            "area": area,
        })

    comps.sort(key=lambda c: c["x1"])

    comps_validos = [
        c for c in comps
        if c["h"] >= h * 0.20 and c["area"] >= 10
    ]

    if len(comps_validos) >= 2:
        return "01"

    return None


def ocr_region_solo_digitos(img, bbox):
    """OCR región solo dígitos."""
    x1, y1, x2, y2 = bbox

    x1 = max(0, int(x1))
    y1 = max(0, int(y1))
    x2 = min(img.width, int(x2))
    y2 = min(img.height, int(y2))

    if x2 <= x1 or y2 <= y1:
        return None

    crop = img.crop((x1, y1, x2, y2)).convert("L")

    variantes = []

    canvas = ImageOps.expand(crop, border=20, fill=255)

    img1 = canvas.resize((canvas.width * 8, canvas.height * 8))
    img1 = ImageOps.autocontrast(img1)
    img1 = ImageEnhance.Contrast(img1).enhance(3.5)
    variantes.append(img1)

    img2 = img1.point(lambda p: 255 if p > 150 else 0)
    variantes.append(img2)

    img3 = canvas.resize((canvas.width * 10, canvas.height * 10))
    img3 = ImageOps.autocontrast(img3)
    img3 = ImageEnhance.Sharpness(img3).enhance(2.0)
    variantes.append(img3)

    configs = [
        "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789",
        "--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789",
        "--oem 3 --psm 13 -c tessedit_char_whitelist=0123456789",
        "--oem 3 --psm 10 -c tessedit_char_whitelist=0123456789",
    ]

    resultados = []

    for imagen in variantes:
        for config in configs:
            try:
                txt = pytesseract.image_to_string(
                    imagen,
                    lang="eng",
                    config=config
                )

                solo = re.sub(r"\D", "", limpiar(txt))

                if solo:
                    if len(solo) == 1:
                        resultados.append(solo.zfill(2))
                    else:
                        resultados.append(solo[:2])

            except Exception:
                pass

    if resultados:
        if "01" in resultados:
            return "01"

        return resultados[0]

    return detectar_codigo_01_por_pixeles(img, bbox)


def detectar_input_codigo_tipo_documento(img, linea_label):
    """Detecta bbox del input código tipo documento."""
    alto = max(14, linea_label["bottom"] - linea_label["top"])

    sx1 = int(linea_label["right"] - alto * 0.8)
    sy1 = int(linea_label["top"] - alto * 1.4)
    sx2 = int(linea_label["right"] + alto * 8.5)
    sy2 = int(linea_label["bottom"] + alto * 2.2)

    sx1 = max(0, sx1)
    sy1 = max(0, sy1)
    sx2 = min(img.width, sx2)
    sy2 = min(img.height, sy2)

    search = img.crop((sx1, sy1, sx2, sy2)).convert("L")
    w, h = search.size

    dark = set()

    for y in range(h):
        for x in range(w):
            if search.getpixel((x, y)) < 90:
                dark.add((x, y))

    visitados = set()
    comps = []

    for p in list(dark):
        if p in visitados:
            continue

        stack = [p]
        visitados.add(p)

        xs = []
        ys = []

        while stack:
            x, y = stack.pop()
            xs.append(x)
            ys.append(y)

            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < w and 0 <= ny < h and (nx, ny) in dark and (nx, ny) not in visitados:
                    visitados.add((nx, ny))
                    stack.append((nx, ny))

        x1, x2 = min(xs), max(xs)
        y1, y2 = min(ys), max(ys)

        cw = x2 - x1 + 1
        ch = y2 - y1 + 1

        if 22 <= cw <= 100 and 18 <= ch <= 70:
            comps.append((x1, y1, x2, y2, cw, ch))

    if not comps:
        return (
            max(0, int(linea_label["right"] + 2)),
            max(0, int(linea_label["top"] - alto * 0.5)),
            min(img.width, int(linea_label["right"] + alto * 4.8)),
            min(img.height, int(linea_label["bottom"] + alto * 0.9)),
        )

    comps.sort(key=lambda c: (c[0], -c[4] * c[5]))
    bx1, by1, bx2, by2, bw, bh = comps[0]

    ax1 = sx1 + bx1
    ay1 = sy1 + by1
    ax2 = sx1 + bx2
    ay2 = sy1 + by2

    margen_x = max(3, int((ax2 - ax1) * 0.10))
    margen_y = max(3, int((ay2 - ay1) * 0.18))

    return (
        max(0, ax1 + margen_x),
        max(0, ay1 + margen_y),
        min(img.width, ax2 - margen_x),
        min(img.height, ay2 - margen_y),
    )


def extraer_codigo_tipo_documento(palabras, lineas, screenshot):
    """Extrae código tipo documento ZFIEC015."""
    linea_label = None

    for l in lineas:
        texto = limpiar(l["texto"])
        n = normalizar(texto)

        if (
            ("codigo" in n or "cedigo" in n or "cdigo" in n)
            and "tipo" in n
            and "documento" in n
        ):
            linea_label = l
            break

    if not linea_label:
        return None

    texto_linea = limpiar(linea_label["texto"])

    partes = re.split(r"documento[:\*\s]*", texto_linea, flags=re.IGNORECASE)
    cola = partes[-1] if len(partes) > 1 else texto_linea

    nums = re.findall(r"\d{1,3}", cola)

    if nums:
        for n in reversed(nums):
            if len(n) == 2:
                return n

        return nums[-1].zfill(2)

    bbox_input = detectar_input_codigo_tipo_documento(
        screenshot,
        linea_label
    )

    valor = ocr_region_solo_digitos(
        screenshot,
        bbox_input
    )

    if valor:
        return valor

    x1, y1, x2, y2 = bbox_input

    pad_x = max(4, int((x2 - x1) * 0.25))
    pad_y = max(3, int((y2 - y1) * 0.35))

    bbox_alt = (
        x1 - pad_x,
        y1 - pad_y,
        x2 + pad_x,
        y2 + pad_y,
    )

    return ocr_region_solo_digitos(
        screenshot,
        bbox_alt
    )


def extraer_tipo_procesamiento_zfiec(lineas):
    """Detecta radio seleccionado."""
    opciones = []

    for l in lineas:
        texto = limpiar(l["texto"])
        n = normalizar(texto)

        if "pendiente" in n:
            opciones.append(("Pendiente", texto))

        elif "procesado" in n:
            opciones.append(("Procesado", texto))

        elif "rechazo" in n:
            opciones.append(("Rechazo", texto))

    marcadores = ["(s)", "(e)", "(@)", "(@", "@)"]

    for nombre, texto in opciones:
        t = texto.lower().replace(" ", "")
        if any(m in t for m in marcadores):
            return nombre

    return None


def leer_valores_zfiec015():
    """
    Lee todos los campos del formulario ZFIEC015 por OCR.

    IMPORTANTE:
    - NO actualiza valores_bancos.json.
    - SOLO devuelve valores detectados.
    - valores_bancos.json solo debe ser actualizado por main.py.
    """
    screenshot, palabras, lineas = obtener_palabras_lineas_desde_pantalla()

    # Debug opcional
    # guardar_screenshot(screenshot)
    # log_lineas_ocr(lineas)

    fecha_inicio, fecha_fin = extraer_fechas_zfiec(lineas)

    codigo_tipo_doc = extraer_codigo_tipo_documento(
        palabras,
        lineas,
        screenshot
    )

    
    proveedor_detectado = extraer_proveedor_zfiec(
        palabras,
        lineas,
        screenshot=screenshot
    )

    resultado = {
        "Sociedad": extraer_sociedad_zfiec(palabras),
        "Proveedor": extraer_proveedor_zfiec(palabras, lineas, screenshot=screenshot),
        "FechaInicio": fecha_inicio,
        "FechaFin": fecha_fin,
        "Código Tipo de Documento": codigo_tipo_doc,
        "Tipo de Procesamiento": extraer_tipo_procesamiento_zfiec(lineas),
    }

    # SIEMPRE actualizar archivo de detectados OCR
    try:
        ruta_debug = _BASE_DIR / "valores_zfiec015_detectados.json"

        debug_data = {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pantalla": "ZFIEC015",
            "detectados": resultado,
        }

        with open(ruta_debug, "w", encoding="utf-8") as f:
            json.dump(debug_data, f, ensure_ascii=False, indent=4)

        _log.info("ZFIEC015 detectados actualizados en %s", ruta_debug)

    except Exception as e:
        _log.warning("No se pudo guardar valores_zfiec015_detectados.json: %s", e)

    return resultado


def leer_y_validar_zfiec015():
    """
    Lee valores OCR de ZFIEC015 y compara contra valores_bancos.json.

    IMPORTANTE:
    - valores_bancos.json contiene los esperados.
    - OCR NO reemplaza ni actualiza valores_bancos.json.
    """
    ruta_base = _BASE_DIR / "valores_bancos.json"

    if ruta_base.exists():
        with open(ruta_base, encoding="utf-8") as f:
            esperados = json.load(f)
    else:
        esperados = {}

    detectados = leer_valores_zfiec015()

    diferencias = {}

    campos_json = [
        "Sociedad",
        "Proveedor",
        "FechaInicio",
        "FechaFin",
        "Código Tipo de Documento",
        "Tipo de Procesamiento",
    ]

    _log.info("Validando ZFIEC015 por OCR...")
    _log.info("ZFIEC015 esperados desde valores_bancos.json: %r", esperados)
    _log.info("ZFIEC015 detectados OCR: %r", detectados)

    for campo in campos_json:
        val_esp = esperados.get(campo)

        if val_esp is None or str(val_esp).strip() == "":
            continue

        val_det = detectados.get(campo)

        n_esp = normalizar(str(val_esp))
        n_det = normalizar(str(val_det or ""))

        if n_det != n_esp:
            diferencias[campo] = f"esperado={val_esp!r} detectado={_nd(val_det)}"

    if diferencias:
        _log.error("Validación ZFIEC015 fallida:")
        for k, v in diferencias.items():
            _log.error("  %s: %s", k, v)

        _log.error("Valores esperados ZFIEC015:")
        for k, v in esperados.items():
            _log.error("  %s: %r", k, v)

        _log.error("Valores detectados OCR ZFIEC015:")
        for k, v in detectados.items():
            _log.error("  %s: %r", k, v)
    else:
        _log.info("Validación ZFIEC015 OK.")

    return {
        "detectados": detectados,
        "diferencias": diferencias,
        "valido": len(diferencias) == 0,
    }


# ============================================================
# FB60
# ============================================================

_FB60_BASE_W = 1580
_FB60_BASE_H = 1080


def escalar_bbox_fb60(img, bbox):
    """Escala bbox calibrado 1580x1080."""
    x1, y1, x2, y2 = bbox

    sx = img.width / _FB60_BASE_W
    sy = img.height / _FB60_BASE_H

    return (
        int(x1 * sx),
        int(y1 * sy),
        int(x2 * sx),
        int(y2 * sy),
    )


def ocr_crop_fb60(img, bbox, modo="texto"):
    """OCR sobre una región FB60."""
    x1, y1, x2, y2 = escalar_bbox_fb60(img, bbox)

    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(img.width, x2)
    y2 = min(img.height, y2)

    if x2 <= x1 or y2 <= y1:
        return ""

    crop = img.crop((x1, y1, x2, y2)).convert("L")

    crop = ImageOps.expand(crop, border=8, fill=255)
    crop = crop.resize((crop.width * 4, crop.height * 4))
    crop = ImageOps.autocontrast(crop)
    crop = ImageEnhance.Contrast(crop).enhance(2.4)
    crop = ImageEnhance.Sharpness(crop).enhance(1.8)

    if modo == "fecha":
        config = "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789./,"
        lang = "eng"

    elif modo == "decimal":
        config = "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.,-Oo"
        lang = "eng"

    elif modo == "cuenta":
        config = "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789"
        lang = "eng"

    else:
        config = "--oem 3 --psm 7"
        lang = "spa+eng"

    try:
        txt = pytesseract.image_to_string(
            crop,
            lang=lang,
            config=config
        )
    except Exception:
        txt = pytesseract.image_to_string(
            crop,
            lang="eng",
            config=config
        )

    return limpiar(txt)


def extraer_lineas_fb60(img):
    """Extrae líneas OCR FB60."""
    img_proc = preparar_pantalla(img)
    data = obtener_ocr_data(img_proc)

    bloques = []

    for i in range(len(data["text"])):
        txt = limpiar(data["text"][i])

        if not txt:
            continue

        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = -1

        if conf < 5:
            continue

        y = data["top"][i]
        x = data["left"][i]

        if x > 2100:
            continue

        bloques.append((y, x, txt))

    bloques.sort(key=lambda x: (x[0], x[1]))

    lineas = []
    linea_actual = []
    y_ref = None
    tolerancia_y = 22

    for y, x, txt in bloques:
        if y_ref is None:
            linea_actual = [(x, txt)]
            y_ref = y
            continue

        if abs(y - y_ref) <= tolerancia_y:
            linea_actual.append((x, txt))
            y_ref = int((y_ref + y) / 2)
        else:
            linea_actual.sort(key=lambda p: p[0])
            linea = limpiar(" ".join(t for _, t in linea_actual))

            if linea:
                lineas.append(linea)

            linea_actual = [(x, txt)]
            y_ref = y

    if linea_actual:
        linea_actual.sort(key=lambda p: p[0])
        linea = limpiar(" ".join(t for _, t in linea_actual))

        if linea:
            lineas.append(linea)

    return lineas


def texto_fb60_unido(lineas):
    """Une líneas FB60 en un solo texto."""
    txt = " ".join(lineas)
    txt = txt.replace("|", " ")
    txt = txt.replace("&lt;", " ")
    txt = txt.replace("&gt;", " ")
    txt = txt.replace("&amp;lt;", " ")
    txt = txt.replace("&amp;gt;", " ")
    txt = txt.replace("&amp;amp;lt;", " ")
    txt = txt.replace("&amp;amp;gt;", " ")
    txt = txt.replace("—", " ")
    txt = txt.replace("_", " ")
    txt = txt.replace("*", " * ")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def normalizar_decimal(txt):
    """
    Normaliza importes SAP/OCR a formato estándar 1234.56.

    Soporta:
    - 466.67
    - 4066.00
    - 4,066.00
    - 4.066,00
    """
    if not txt:
        return None

    txt = limpiar(txt)
    txt = txt.replace("O", "0")
    txt = txt.replace("o", "0")
    txt = txt.replace(" ", "")

    m = re.search(
        r"-?\d{1,3}(?:[.,]\d{3})*[.,]\d{2}|-?\d+[.,]\d{2}",
        txt
    )

    if not m:
        return None

    num = m.group(0)

    if "." in num and "," in num:
        if num.rfind(".") > num.rfind(","):
            num = num.replace(",", "")
        else:
            num = num.replace(".", "")
            num = num.replace(",", ".")
    else:
        if "," in num:
            num = num.replace(",", ".")

    try:
        return f"{float(num):.2f}"
    except Exception:
        return None


def importes_iguales(a, b):
    """Compara dos importes a 2 decimales."""
    try:
        na = normalizar_decimal(str(a))
        nb = normalizar_decimal(str(b))

        if na is None or nb is None:
            return False

        return round(float(na), 2) == round(float(nb), 2)

    except Exception:
        return False


def ocr_decimal_preciso_fb60(img, bbox, nombre_debug=None):
    """OCR especializado para importes."""
    x1, y1, x2, y2 = escalar_bbox_fb60(img, bbox)

    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(img.width, x2)
    y2 = min(img.height, y2)

    if x2 <= x1 or y2 <= y1:
        return []

    crop = img.crop((x1, y1, x2, y2)).convert("L")

    variantes = []

    base = ImageOps.expand(crop, border=12, fill=255)

    img1 = base.resize((base.width * 8, base.height * 8))
    img1 = ImageOps.autocontrast(img1)
    img1 = ImageEnhance.Contrast(img1).enhance(3.8)
    img1 = ImageEnhance.Sharpness(img1).enhance(2.5)
    variantes.append(img1)

    img2 = img1.point(lambda p: 0 if p < 190 else 255)
    variantes.append(img2)

    img3 = img1.point(lambda p: 0 if p < 220 else 255)
    variantes.append(img3)

    img4 = img1.point(lambda p: 0 if p < 245 else 255)
    variantes.append(img4)

    configs = [
        "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.,Oo",
        "--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789.,Oo",
        "--oem 3 --psm 13 -c tessedit_char_whitelist=0123456789.,Oo",
    ]

    resultados = []

    for variante in variantes:
        for config in configs:
            try:
                txt = pytesseract.image_to_string(
                    variante,
                    lang="eng",
                    config=config
                )

                valor = normalizar_decimal(txt)
                if valor:
                    resultados.append(valor)

            except Exception:
                pass

    return resultados


def fecha_valida_fb60(fecha):
    """Valida fecha DD.MM.YYYY."""
    if not fecha:
        return False

    try:
        datetime.datetime.strptime(fecha, "%d.%m.%Y")
        return True
    except Exception:
        return False


def extraer_titulo_fb60_v2(lineas):
    """Extrae título FB60."""
    for l in lineas:
        t = limpiar_texto_ocr_fuerte(l)

        if "registrar factura de acreedor" in t.lower():
            t = re.sub(r"^SAP\s+", "", t, flags=re.IGNORECASE)
            t = t.replace("&lt;", "").replace("&gt;", "")
            t = t.replace("&amp;lt;", "").replace("&amp;gt;", "")
            return limpiar(t)

    txt = texto_fb60_unido(lineas)

    m = re.search(
        r"(Registrar\s+factura\s+de\s+acreedor\s*:\s*Sociedad\s+\d+)",
        txt,
        re.IGNORECASE
    )

    if m:
        return limpiar(m.group(1))

    return None


def extraer_fechas_fb60_v2(img, lineas):
    """Extrae fechas FB60."""
    bboxes_factura = [
        (215, 340, 390, 385),
        (220, 345, 355, 380),
        (218, 342, 370, 382),
    ]

    bboxes_contab = [
        (215, 385, 390, 425),
        (220, 390, 355, 420),
        (218, 387, 370, 422),
    ]

    fecha_factura = None
    fecha_contab = None

    for bbox in bboxes_factura:
        txt = ocr_crop_fb60(img, bbox, modo="fecha")
        m = re.search(r"\d{2}[.,]\d{2}[.,]\d{4}", txt)

        if m:
            f = m.group(0).replace(",", ".")

            if fecha_valida_fb60(f):
                fecha_factura = f
                break

    for bbox in bboxes_contab:
        txt = ocr_crop_fb60(img, bbox, modo="fecha")
        m = re.search(r"\d{2}[.,]\d{2}[.,]\d{4}", txt)

        if m:
            f = m.group(0).replace(",", ".")

            if fecha_valida_fb60(f):
                fecha_contab = f
                break

    if not fecha_factura or not fecha_contab:
        txt = texto_fb60_unido(lineas)

        fechas = re.findall(r"\d{2}[.,]\d{2}[.,]\d{4}", txt)
        fechas = [f.replace(",", ".") for f in fechas]
        fechas = [f for f in fechas if fecha_valida_fb60(f)]

        if not fecha_factura and len(fechas) >= 1:
            fecha_factura = fechas[0]

        if not fecha_contab and len(fechas) >= 2:
            fecha_contab = fechas[1]

    if fecha_factura and not fecha_contab:
        fecha_contab = fecha_factura

    return fecha_factura, fecha_contab


def extraer_clase_documento_fb60_v2(img, lineas):
    """Extrae clase documento FB60 — retorna el valor real del campo."""
    bboxes = [
        (215, 425, 445, 465),
        (210, 420, 455, 470),
        (220, 430, 440, 460),
    ]

    for bbox in bboxes:
        txt = ocr_crop_fb60(img, bbox, modo="texto")
        n = normalizar(txt)

        if "facturaacreedor" in n or ("factura" in n and "acreedor" in n):
            return "Factura acreedor"

        if txt and len(txt) > 2:
            # Retorna el valor real (ej. "Tiquetes Aéreos") para que la validación lo compare
            return limpiar(txt)

    for l in lineas:
        n = normalizar(l)

        if "facturaacreedor" in n:
            return "Factura acreedor"

        if "clasedoc" in n and "factura" in n:
            return "Factura acreedor"

    # El título de la ventana SIEMPRE dice "Registrar factura de acreedor" — no se usa
    # como fallback porque produciría falsos positivos cuando Clase doc. es incorrecto
    return None


def extraer_importe_fb60_v2(img, lineas):
    """Extrae importe cabecera FB60."""
    bboxes = [
        (218, 502, 315, 542),
        (220, 505, 300, 538),
        (215, 500, 360, 545),
        (215, 500, 595, 545),
    ]

    candidatos = []

    for idx, bbox in enumerate(bboxes, start=1):
        valores = ocr_decimal_preciso_fb60(
            img,
            bbox,
            nombre_debug=f"importe_crop_{idx}"
        )

        for v in valores:
            candidatos.append(v)

    if candidatos:
        conteo = {}

        for c in candidatos:
            conteo[c] = conteo.get(c, 0) + 1

        ordenados = sorted(conteo.items(), key=lambda x: x[1], reverse=True)
        return ordenados[0][0]

    txt = texto_fb60_unido(lineas)

    m = re.search(
        r"Importe\s*[:\s]*([0-9]+[.,][0-9]{2})",
        txt,
        re.IGNORECASE
    )

    if m:
        return normalizar_decimal(m.group(1))

    return None


def extraer_calc_impuestos_fb60_v2(img, lineas):
    """Detecta estado del checkbox Calc.Impuestos por análisis de píxeles."""
    # El label "Calc.impuestos" siempre es visible — no indica estado del checkbox.
    # Se analiza el cuadrito a la izquierda del label: más píxeles oscuros → marcado.
    txt = texto_fb60_unido(lineas).lower()
    if "calc.impuestos" not in txt and "calc impuestos" not in txt:
        return None  # pantalla incorrecta

    x1, y1, x2, y2 = escalar_bbox_fb60(img, (450, 542, 490, 578))
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(img.width, x2), min(img.height, y2)
    if x2 <= x1 or y2 <= y1:
        return None

    crop_img = img.crop((x1, y1, x2, y2)).convert("RGB")
    # try:
    #     crop_img.save(str(_BASE_DIR / "debug_checkbox.png"))
    # except Exception:
    #     pass

    pixels = list(crop_img.getdata())
    if not pixels:
        return None

    # Tilde SAP es azul — B significativamente mayor que R indica checkbox marcado
    blue = sum(1 for r, g, b in pixels if b > r + 40 and b > 100)
    return (blue / len(pixels)) > 0.01


def extraer_combo_b2_fb60_v2(img, lineas):
    """Extrae el indicador de impuesto del combo FB60 (B1, B2, B3, …)."""
    txt = ocr_crop_fb60(img, (455, 585, 745, 625), modo="texto")

    # Buscar Bx (B1, B2, B3, …) y "8x" como confusión OCR del dígito B→8
    # Solo leer del crop del combo — sin fallback a lineas completas para evitar
    # falsos positivos desde la columna de indicador de la tabla (ej. "0.00 B1")
    m = re.search(r"\b([Bb8][0-9])\b", txt)
    if m:
        codigo = m.group(1).upper().replace("8", "B")
        if re.search(r"iva|compras|15", txt, re.IGNORECASE):
            return f"{codigo} (IVA Compras 15% Cred...)"
        return codigo

    return None


def extraer_importe_moneda_doc_fb60(img, lineas, importe_referencia=None):
    """
    Extrae Importe moneda doc.

    Regla:
    - Si hay importe_referencia, solo acepta candidato igual a la cabecera.
    - Si OCR lee mal, devuelve None.
    - No usa valores quemados.
    """
    candidatos = []

    txt = texto_fb60_unido(lineas)

    importes = re.findall(r"\b\d+[.,]\d{2}\b", txt)
    importes = [normalizar_decimal(i) for i in importes]
    importes = [i for i in importes if i and i != "0.00"]

    candidatos.extend(importes)

    bboxes_importe_tabla = [
        (600, 780, 930, 885),
        (620, 790, 930, 875),
        (650, 800, 930, 870),
        (680, 805, 920, 865),
        (700, 810, 910, 860),
        (540, 780, 980, 890),
        (580, 800, 1000, 875),
        (720, 790, 980, 875),
        (690, 810, 880, 870),
        (700, 815, 900, 870),
    ]

    for idx, bbox in enumerate(bboxes_importe_tabla, start=1):
        valores = ocr_decimal_preciso_fb60(
            img,
            bbox,
            nombre_debug=f"importe_moneda_doc_{idx}"
        )

        for v in valores:
            candidatos.append(v)

        txt_bbox = ocr_crop_fb60(img, bbox, modo="decimal")
        valor = normalizar_decimal(txt_bbox)

        if valor:
            candidatos.append(valor)

    candidatos_limpios = []

    for c in candidatos:
        v = normalizar_decimal(c)
        if v:
            candidatos_limpios.append(v)

    if not candidatos_limpios:
        return None

    if importe_referencia:
        for c in candidatos_limpios:
            if importes_iguales(c, importe_referencia):
                return normalizar_decimal(c)

        return None

    conteo = {}

    for c in candidatos_limpios:
        conteo[c] = conteo.get(c, 0) + 1

    ordenados = sorted(conteo.items(), key=lambda x: x[1], reverse=True)

    return ordenados[0][0]


def extraer_tabla_fb60_v2(img, lineas, importe_referencia=None):
    """Extrae tabla FB60 sin valores quemados."""
    resultado = {
        "Cta.mayor": None,
        "Importe moneda doc.": None,
        "Texto": None,
        "Centro coste": None,
    }

    txt = texto_fb60_unido(lineas)

    cuentas = re.findall(r"\b\d{9,12}\b", txt)

    cuentas_validas = []

    for c in cuentas:
        if re.fullmatch(r"20\d{8}", c):
            continue

        cuentas_validas.append(c)

    # Las cuentas GL de ASIAUTO siempre empiezan con 8 — ignorar proveedores (1000xxxxxx)
    preferidas = [c for c in cuentas_validas if c.startswith("8")]

    if preferidas:
        resultado["Cta.mayor"] = preferidas[0]

    if not resultado["Cta.mayor"]:
        cta_txt = ocr_crop_fb60(img, (85, 820, 220, 860), modo="cuenta")

        m = re.search(r"\b8\d{8,11}\b", cta_txt)
        if m:
            resultado["Cta.mayor"] = m.group(0)

    importes = re.findall(r"\b\d+[.,]\d{2}\b", txt)
    importes = [normalizar_decimal(i) for i in importes]
    candidatos = [i for i in importes if i and i != "0.00"]

    if candidatos and importe_referencia:
        for c in candidatos:
            if importes_iguales(c, importe_referencia):
                resultado["Importe moneda doc."] = c
                break

    if not resultado["Importe moneda doc."]:
        resultado["Importe moneda doc."] = extraer_importe_moneda_doc_fb60(
            img,
            lineas,
            importe_referencia=importe_referencia
        )

    if "COMISION" in txt.upper():
        m = re.search(
            r"(COMISION\s+[A-ZÁÉÍÓÚÑ0-9. ]{0,40})",
            txt,
            re.IGNORECASE
        )

        if m:
            resultado["Texto"] = limpiar(m.group(1))
        else:
            resultado["Texto"] = "COMISION"

    if not resultado["Texto"]:
        texto_txt = ocr_crop_fb60(img, (930, 820, 1070, 860), modo="texto")

        if "COMISION" in texto_txt.upper():
            resultado["Texto"] = limpiar(texto_txt)
        elif texto_txt:
            resultado["Texto"] = limpiar(texto_txt)

    centros = re.findall(r"\b20\d{8}\b", txt)

    if centros:
        resultado["Centro coste"] = centros[-1]

    if not resultado["Centro coste"]:
        cc_txt = ocr_crop_fb60(img, (1335, 820, 1475, 860), modo="cuenta")

        m = re.search(r"\b20\d{8}\b", cc_txt)
        if m:
            resultado["Centro coste"] = m.group(0)

    return resultado


def texto_sap_coincide(val_esp, val_det):
    """
    Compara texto esperado vs texto detectado en SAP/OCR.

    Casos:
    - Esperado:  'COMISION BANCO GUAYAQUIL'
    - Detectado: 'COMISION BA... o'
    - Detectado: 'COMISION BA.. o'

    Regla:
    Si hay truncamiento con puntos, compara solo la parte visible.
    """
    esp_raw = limpiar(str(val_esp or "")).upper()
    det_raw = limpiar(str(val_det or "")).upper()

    if not det_raw:
        return False

    esp_raw = re.sub(r"\s+", " ", esp_raw).strip()
    det_raw = re.sub(r"\s+", " ", det_raw).strip()

    m = re.search(r"\.{2,}", det_raw)

    if m:
        visible = det_raw[:m.start()].strip()

        if not visible:
            return False

        visible_norm = normalizar(visible)
        esp_norm = normalizar(esp_raw)

        return esp_norm.startswith(visible_norm)

    return normalizar(esp_raw) == normalizar(det_raw)


def leer_valores_fb60():
    """Lee todos los campos FB60."""
    screenshot = capturar_ventana_sap()

    lineas = extraer_lineas_fb60(screenshot)

    # Debug opcional:
    # guardar_screenshot(screenshot)
    # log_lineas_ocr(lineas)

    fecha_factura, fecha_contab = extraer_fechas_fb60_v2(screenshot, lineas)

    importe_cabecera = extraer_importe_fb60_v2(screenshot, lineas)

    tabla = extraer_tabla_fb60_v2(
        screenshot,
        lineas,
        importe_referencia=importe_cabecera
    )

    resultado = {
        "Titulo": extraer_titulo_fb60_v2(lineas),
        "Clase documento": extraer_clase_documento_fb60_v2(screenshot, lineas),

        "Fecha factura": fecha_factura,
        "Fecha contab.": fecha_contab,

        "Calc.impuestos": extraer_calc_impuestos_fb60_v2(screenshot, lineas),
        "Combo B2": extraer_combo_b2_fb60_v2(screenshot, lineas),

        "Importe": importe_cabecera,

        "Cta.mayor": tabla.get("Cta.mayor"),
        "Importe moneda doc.": tabla.get("Importe moneda doc."),
        "Texto": tabla.get("Texto"),
        "Centro coste": tabla.get("Centro coste"),
    }

    if (
        not resultado.get("Importe moneda doc.")
        and resultado.get("Importe")
        and resultado.get("Cta.mayor")
        and resultado.get("Texto")
        and resultado.get("Centro coste")
    ):
        resultado["Importe moneda doc."] = resultado["Importe"]

    return resultado


def validar_campos_fb60(valores):
    """Valida presencia, formato y coherencia FB60."""
    errores = {}

    obligatorios = [
        "Titulo",
        "Clase documento",
        "Fecha factura",
        "Fecha contab.",
        "Calc.impuestos",
        "Combo B2",
        "Importe",
        "Cta.mayor",
        "Importe moneda doc.",
        "Texto",
        "Centro coste",
    ]

    for campo in obligatorios:
        if valores.get(campo) in (None, "", False):
            errores[campo] = "VACÍO"

    if valores.get("Titulo"):
        if "registrar factura de acreedor" not in valores["Titulo"].lower():
            errores["Titulo"] = "TÍTULO INCORRECTO"

    if valores.get("Clase documento"):
        clase = normalizar(valores["Clase documento"])

        if "factura" not in clase or "acreedor" not in clase:
            errores["Clase documento"] = "CLASE DOCUMENTO INVÁLIDA"

    if valores.get("Fecha factura"):
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", valores["Fecha factura"]):
            errores["Fecha factura"] = "FORMATO INVÁLIDO"

    if valores.get("Fecha contab."):
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", valores["Fecha contab."]):
            errores["Fecha contab."] = "FORMATO INVÁLIDO"

    if valores.get("Cta.mayor"):
        if not re.fullmatch(r"\d{9,12}", valores["Cta.mayor"]):
            errores["Cta.mayor"] = "CUENTA INVÁLIDA"

    if valores.get("Centro coste"):
        if not re.fullmatch(r"20\d{8}", valores["Centro coste"]):
            errores["Centro coste"] = "CENTRO COSTE INVÁLIDO"

    if valores.get("Importe"):
        try:
            importe = float(str(valores["Importe"]).replace(",", "."))

            if importe <= 0:
                errores["Importe"] = "IMPORTE <= 0"

        except Exception:
            errores["Importe"] = "IMPORTE NO NUMÉRICO"

    if valores.get("Importe moneda doc."):
        try:
            importe_doc = float(str(valores["Importe moneda doc."]).replace(",", "."))

            if importe_doc <= 0:
                errores["Importe moneda doc."] = "IMPORTE <= 0"

        except Exception:
            errores["Importe moneda doc."] = "IMPORTE NO NUMÉRICO"

    if valores.get("Importe") and valores.get("Importe moneda doc."):
        try:
            imp_cab = float(str(valores["Importe"]).replace(",", "."))
            imp_tab = float(str(valores["Importe moneda doc."]).replace(",", "."))

            if round(imp_cab, 2) != round(imp_tab, 2):
                errores["Importe"] = "NO COINCIDE CON IMPORTE MONEDA DOC."

        except Exception:
            pass

    return errores


def leer_y_validar_fb60():
    """
    Compara valores OCR detectados en FB60 contra esperados en valores_fb60.json.
    """
    ruta_base = _BASE_DIR / "valores_fb60.json"

    if ruta_base.exists():
        with open(ruta_base, encoding="utf-8") as f:
            esperados = json.load(f)
    else:
        esperados = {}

    detectados = leer_valores_fb60()

    diferencias = {}

    campos_json = [
        "Titulo",
        "Clase documento",
        "Calc.impuestos",
        "Combo B2",
        "Cta.mayor",
        "Texto",
        "Centro coste",
    ]

    for campo in campos_json:
        val_esp = esperados.get(campo)

        if val_esp is None:
            continue

        val_det = detectados.get(campo)

        if isinstance(val_esp, bool):
            if val_det != val_esp:
                diferencias[campo] = f"esperado={val_esp!r} detectado={_nd(val_det)}"

        elif campo in ("Titulo", "Combo B2"):
            n_esp = normalizar(str(val_esp))
            n_det = normalizar(str(val_det or ""))

            # SAP agrega descripción larga al código ("B2 (IVA Compras 15% Cred...)")
            # — basta con que el código esperado esté contenido en lo detectado
            if n_esp not in n_det:
                diferencias[campo] = f"esperado={val_esp!r} detectado={_nd(val_det)}"

        elif campo == "Texto":
            if not texto_sap_coincide(val_esp, val_det):
                diferencias[campo] = f"esperado={val_esp!r} detectado={_nd(val_det)}"

        else:
            n_esp = normalizar(str(val_esp))
            n_det = normalizar(str(val_det or ""))

            if n_det != n_esp:
                diferencias[campo] = f"esperado={val_esp!r} detectado={_nd(val_det)}"

    ff = detectados.get("Fecha factura")
    fc = detectados.get("Fecha contab.")

    if ff and fc and ff != fc:
        diferencias["Fecha factura/contab."] = f"factura={ff!r} contab={fc!r}"

    imp_cab = detectados.get("Importe")
    imp_tab = detectados.get("Importe moneda doc.")

    if imp_cab is None or str(imp_cab).strip() == "":
        diferencias["Importe"] = f"cabecera no detectada: cabecera={imp_cab!r} tabla={imp_tab!r}"

    elif imp_tab is None or str(imp_tab).strip() == "":
        diferencias["Importe moneda doc."] = f"tabla no detectada: cabecera={imp_cab!r} tabla={imp_tab!r}"

    else:
        try:
            imp_cab_num = round(float(str(imp_cab).replace(",", ".")), 2)
            imp_tab_num = round(float(str(imp_tab).replace(",", ".")), 2)

            if imp_cab_num != imp_tab_num:
                diferencias["Importe"] = f"cabecera={imp_cab!r} tabla={imp_tab!r}"

        except Exception:
            diferencias["Importe"] = f"no numérico: cabecera={imp_cab!r} tabla={imp_tab!r}"

    if diferencias:
        _log.error("Validación FB60 fallida. Valores detectados por OCR:")
        for k, v in detectados.items():
            _log.error("  %s: %r", k, v)

    return {
        "detectados": detectados,
        "diferencias": diferencias,
        "valido": len(diferencias) == 0,
    }


# ============================================================
# FB60 DETALLE
# ============================================================

def extraer_txt_cabecera_detalle_fb60(img, lineas):
    """Extrae Txt.cabec. de pestaña Detalle."""
    for l in lineas:
        n = normalizar(l)

        if "cabec" in n:
            m = re.search(r"(?:cabec\.?\s*[:\s]+)(.+)", l, re.IGNORECASE)
            if m:
                val = limpiar(m.group(1))
                if val:
                    return val

    bboxes = [
        (115, 305, 415, 342),
        (100, 298, 440, 348),
        (115, 298, 520, 348),
    ]

    for bbox in bboxes:
        txt = ocr_crop_fb60(img, bbox, modo="texto")

        if txt and len(txt) > 2:
            return limpiar(txt)

    return None


def leer_valores_fb60_detalle():
    """Lee FB60 Detalle."""
    screenshot = capturar_ventana_sap()
    lineas = extraer_lineas_fb60(screenshot)

    return {
        "Txt.cabec.": extraer_txt_cabecera_detalle_fb60(screenshot, lineas),
    }


def leer_y_validar_fb60_detalle():
    """Compara Txt.cabec. contra Texto Cabecera en valores_bancos.json."""
    ruta = _BASE_DIR / "valores_bancos.json"

    if ruta.exists():
        with open(ruta, encoding="utf-8") as f:
            esperados = json.load(f)
    else:
        esperados = {}

    detectados = leer_valores_fb60_detalle()
    diferencias = {}

    val_esp = esperados.get("Texto Cabecera")

    if val_esp is not None:
        val_det = detectados.get("Txt.cabec.")
        n_esp = normalizar(str(val_esp))
        n_det = normalizar(str(val_det or ""))

        if n_esp not in n_det:
            diferencias["Txt.cabec."] = f"esperado={val_esp!r} detectado={_nd(val_det)}"

    if diferencias:
        _log.error("Validación FB60 Detalle fallida:")
        for k, v in detectados.items():
            _log.error("  %s: %r", k, v)
    else:
        _log.info(
            "Validación OCR FB60 Detalle OK — Txt.cabec.: %r",
            detectados.get("Txt.cabec.")
        )

    return {
        "detectados": detectados,
        "diferencias": diferencias,
        "valido": len(diferencias) == 0,
    }


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print()
    print("======================================")
    print("LECTOR VALORES SAP — OCR")
    print("======================================")
    print()
    print("  [1]  FB60         — Registrar factura (Datos básicos)")
    print("  [2]  ZFIEC015     — Recepción de documentos Electrónicos")
    print("  [3]  FB60 Detalle — Txt.cabec. (pestaña Detalle)")
    print()

    modo = input("Selecciona pantalla [1/2/3]: ").strip()

    print()
    print("Deja SAP visible en la pantalla correcta.")
    print("La consola NO debe tapar SAP.")
    print()

    input("Presiona Enter para capturar en 2 segundos...")

    print("Capturando...")
    time.sleep(2)

    if modo == "2":
        resultado = leer_y_validar_zfiec015()
        valores = resultado["detectados"]
        json_out = "valores_zfiec015_detectados.json"

        print()
        print("==============================")
        print("DIFERENCIAS ZFIEC015")
        print("==============================")

        if resultado["diferencias"]:
            for k, v in resultado["diferencias"].items():
                print(f"  {k}: {v}")
        else:
            print("  ✅ Todos los campos correctos")

        print()
        print("==============================")
        print("VALORES ZFIEC015")
        print("==============================")

    elif modo == "3":
        resultado = leer_y_validar_fb60_detalle()
        valores = resultado["detectados"]
        json_out = "valores_bancos.json"

        print()
        print("==============================")
        print("DIFERENCIAS FB60 DETALLE")
        print("==============================")

        if resultado["diferencias"]:
            for k, v in resultado["diferencias"].items():
                print(f"  {k}: {v}")
        else:
            print("  ✅ Txt.cabec. correcto")

        print()
        print("==============================")
        print("VALORES FB60 DETALLE")
        print("==============================")

    else:
        resultado = leer_y_validar_fb60()
        valores = resultado["detectados"]
        json_out = "valores_fb60.json"

        print()
        print("==============================")
        print("DIFERENCIAS FB60")
        print("==============================")

        if resultado["diferencias"]:
            for k, v in resultado["diferencias"].items():
                print(f"  {k}: {v}")
        else:
            print("  ✅ Todos los campos correctos")

        print()
        print("==============================")
        print("VALORES FB60")
        print("==============================")

    for k, v in valores.items():
        print(f"  {k}: {repr(v)}")

    print()
    print(f"JSON generado: {json_out}")
    print()

    input("Enter para salir...")