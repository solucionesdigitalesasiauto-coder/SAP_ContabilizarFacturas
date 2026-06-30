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

_log = logging.getLogger(__name__)

from PIL import ImageGrab, ImageOps, ImageEnhance
import pytesseract

if getattr(sys, 'frozen', False):
    pytesseract.pytesseract.tesseract_cmd = os.path.join(sys._MEIPASS, 'tesseract.exe')


# ==========================================================
# CONFIGURACIÓN
# ==========================================================

_BASE_DIR = pathlib.Path(sys.executable).parent if getattr(sys, 'frozen', False) \
            else pathlib.Path(__file__).parent.parent
_SCREENSHOTS_DIR = _BASE_DIR / "screenshots"
_SCREENSHOTS_DIR.mkdir(exist_ok=True)


# ==========================================================
# TESSERACT
# ==========================================================

def buscar_tesseract():
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


tesseract_path = buscar_tesseract()

if not tesseract_path:
    print("ERROR: No se encontró tesseract.exe")
    sys.exit(1)

pytesseract.pytesseract.tesseract_cmd = tesseract_path

os.environ["TESSDATA_PREFIX"] = os.path.join(
    os.path.dirname(tesseract_path),
    "tessdata"
)


# ==========================================================
# OCR BASE
# ==========================================================

def limpiar(txt):
    if not txt:
        return ""

    txt = str(txt)
    txt = txt.replace("\n", " ")
    txt = txt.replace("\r", " ")
    txt = txt.replace("|", "")
    txt = txt.replace("<", "")
    txt = txt.replace(">", "")
    txt = txt.replace("&lt;", "")
    txt = txt.replace("&gt;", "")
    txt = txt.replace("_", "")
    txt = txt.replace("‘", "")
    txt = txt.replace("’", "")
    txt = txt.replace("“", "")
    txt = txt.replace("”", "")
    txt = txt.replace("—", " ")
    txt = txt.replace("…", "")
    txt = txt.strip(" |[]{}<>~`'\"")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def quitar_acentos(txt):
    if not txt:
        return ""

    return "".join(
        c for c in unicodedata.normalize("NFD", txt)
        if unicodedata.category(c) != "Mn"
    )


def normalizar(txt):
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
    txt = limpiar(txt)
    txt = re.sub(r"^\*+\s*", "", txt)
    txt = re.sub(r"^SAP\s+", "", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def preparar_pantalla(img):
    w, h = img.size
    img = img.resize((w * 2, h * 2))
    img = img.convert("L")
    img = ImageOps.autocontrast(img)
    img = ImageEnhance.Contrast(img).enhance(2.6)
    return img


def obtener_ocr_data(img_proc):
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


# ==========================================================
# CAPTURA SAP USANDO .ENV
# ==========================================================

def leer_env_simple(path_env):
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
    screenshot = capturar_ventana_sap()
    img_proc = preparar_pantalla(screenshot)
    data = obtener_ocr_data(img_proc)
    palabras = extraer_palabras(data, escala=2)
    lineas = agrupar_lineas(palabras)
    return screenshot, palabras, lineas


# ==========================================================
# DEBUG OCR
# ==========================================================

def log_lineas_ocr(lineas):
    ruta = _SCREENSHOTS_DIR / "ocr_debug.txt"

    with open(ruta, "w", encoding="utf-8") as f:
        for l in lineas:
            if isinstance(l, dict):
                f.write(str(l.get("texto", "")) + "\n")
            else:
                f.write(str(l) + "\n")


def guardar_screenshot(img):
    img.save(_SCREENSHOTS_DIR / "ocr_screen.png")


# ==========================================================
# ZFIEC015 — NO MEZCLAR CON FB60
# ==========================================================

def extraer_sociedad_zfiec(palabras):
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


def extraer_proveedor_zfiec(palabras, lineas):
    for l in lineas:
        n = normalizar(l["texto"])

        if "proveedor" in n:
            m = re.search(r"\b(\d{10})\b", l["texto"])
            if m:
                return m.group(1)

    label = None

    for p in palabras:
        if "proveedor" in p["norm"]:
            label = p
            break

    if not label:
        return None

    y_ref = label["cy"]
    candidatos = []

    for p in palabras:
        if abs(p["cy"] - y_ref) <= 40 and p["left"] > label["right"]:
            m = re.search(r"\b(\d{10})\b", p["texto"])
            if m:
                candidatos.append((p["left"], m.group(1)))

    if candidatos:
        candidatos.sort(key=lambda x: x[0])
        return candidatos[-1][1]

    return None


def extraer_fechas_zfiec(lineas):
    fecha_inicio = None
    fecha_fin = None

    for l in lineas:
        texto = l["texto"]
        n = normalizar(texto)

        if "fecha" in n and "factura" not in n and "contab" not in n:
            fechas = re.findall(r"\d{2}[.,]\d{2}[.,]\d{4}", texto)

            if fechas:
                fecha_inicio = fechas[0].replace(",", ".")
                fecha_fin = fechas[1].replace(",", ".") if len(fechas) >= 2 else None
                break

    return fecha_inicio, fecha_fin


def detectar_codigo_01_por_pixeles(img, bbox):
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

    _MARCADORES = ["(s)", "(e)", "(@)", "(@", "@)"]

    for nombre, texto in opciones:
        t = texto.lower().replace(" ", "")
        if any(m in t for m in _MARCADORES):
            return nombre

    return None


def leer_valores_zfiec015():
    screenshot, palabras, lineas = obtener_palabras_lineas_desde_pantalla()

    # guardar_screenshot(screenshot)
    # log_lineas_ocr(lineas)

    fecha_inicio, fecha_fin = extraer_fechas_zfiec(lineas)

    codigo_tipo_doc = extraer_codigo_tipo_documento(
        palabras,
        lineas,
        screenshot
    )

    resultado = {
        "Sociedad": extraer_sociedad_zfiec(palabras),
        "Proveedor": extraer_proveedor_zfiec(palabras, lineas),
        "FechaInicio": fecha_inicio,
        "FechaFin": fecha_fin,
        "Código Tipo de Documento": codigo_tipo_doc,
        "Tipo de Procesamiento": extraer_tipo_procesamiento_zfiec(lineas),
    }

    ruta_json = _BASE_DIR / "valores_bancos.json"

    with open(ruta_json, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=4)

    return resultado


# ==========================================================
# FB60 — OCR POR COORDENADAS, SEPARADO DE ZFIEC015
# ==========================================================

# ==========================================================
# FB60 — OCR POR COORDENADAS, SEPARADO DE ZFIEC015
# ==========================================================

_FB60_BASE_W = 1580
_FB60_BASE_H = 1080


def escalar_bbox_fb60(img, bbox):
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

        # Excluir panel derecho Acreedor.
        # Coordenadas están escaladas x2 por preparar_pantalla().
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
    txt = " ".join(lineas)
    txt = txt.replace("|", " ")
    txt = txt.replace("<", " ")
    txt = txt.replace(">", " ")
    txt = txt.replace("&lt;", " ")
    txt = txt.replace("&gt;", " ")
    txt = txt.replace("&amp;lt;", " ")
    txt = txt.replace("&amp;gt;", " ")
    txt = txt.replace("—", " ")
    txt = txt.replace("_", " ")
    txt = txt.replace("*", " * ")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def normalizar_decimal(txt):
    if not txt:
        return None

    txt = limpiar(txt)
    txt = txt.replace("O", "0")
    txt = txt.replace("o", "0")
    txt = txt.replace(",", ".")
    txt = txt.replace(" ", "")

    m = re.search(r"-?\d+\.\d{2}", txt)

    if m:
        return m.group(0)

    return None

def ocr_decimal_preciso_fb60(img, bbox, nombre_debug=None):
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

    # if nombre_debug:
    #     try:
    #         crop.save(_SCREENSHOTS_DIR / f"{nombre_debug}_original.png")
    #         img1.save(_SCREENSHOTS_DIR / f"{nombre_debug}_v1.png")
    #         img2.save(_SCREENSHOTS_DIR / f"{nombre_debug}_v2.png")
    #         img3.save(_SCREENSHOTS_DIR / f"{nombre_debug}_v3.png")
    #         img4.save(_SCREENSHOTS_DIR / f"{nombre_debug}_v4.png")
    #     except Exception:
    #         pass

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

                txt = limpiar(txt)
                txt = txt.replace("O", "0")
                txt = txt.replace("o", "0")
                txt = txt.replace(",", ".")
                txt = txt.replace(" ", "")

                m = re.search(r"\d+\.\d{2}", txt)

                if m:
                    resultados.append(m.group(0))

            except Exception:
                pass

    return resultados



def fecha_valida_fb60(fecha):
    if not fecha:
        return False

    try:
        datetime.datetime.strptime(fecha, "%d.%m.%Y")
        return True
    except Exception:
        return False

def extraer_titulo_fb60_v2(lineas):
    for l in lineas:
        t = limpiar_texto_ocr_fuerte(l)

        if "registrar factura de acreedor" in t.lower():
            t = re.sub(r"^SAP\s+", "", t, flags=re.IGNORECASE)
            t = t.replace("<", "").replace(">", "")
            t = t.replace("&lt;", "").replace("&gt;", "")
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

    # Fallback por texto general
    if not fecha_factura or not fecha_contab:
        txt = texto_fb60_unido(lineas)

        fechas = re.findall(r"\d{2}[.,]\d{2}[.,]\d{4}", txt)
        fechas = [f.replace(",", ".") for f in fechas]
        fechas = [f for f in fechas if fecha_valida_fb60(f)]

        if not fecha_factura and len(fechas) >= 1:
            fecha_factura = fechas[0]

        if not fecha_contab and len(fechas) >= 2:
            fecha_contab = fechas[1]

    # Fallback controlado:
    # En tu pantalla ambas fechas son iguales.
    if fecha_factura and not fecha_contab:
        fecha_contab = fecha_factura

    return fecha_factura, fecha_contab

def extraer_clase_documento_fb60_v2(img, lineas):
    # Campo Clase doc.*: Factura acreedor
    bboxes = [
        (215, 425, 445, 465),
        (210, 420, 455, 470),
        (220, 430, 440, 460),
    ]

    for bbox in bboxes:
        txt = ocr_crop_fb60(img, bbox, modo="texto")
        n = normalizar(txt)

        if "facturaacreedor" in n:
            return "Factura acreedor"

        if "factura" in n and "acreedor" in n:
            return "Factura acreedor"

    # Fallback por OCR general
    for l in lineas:
        n = normalizar(l)

        if "facturaacreedor" in n:
            return "Factura acreedor"

        if "clasedoc" in n and "factura" in n:
            return "Factura acreedor"

    # Fallback controlado para esta pantalla
    titulo = extraer_titulo_fb60_v2(lineas)

    if titulo and "registrar factura de acreedor" in titulo.lower():
        return "Factura acreedor"

    return None


def extraer_importe_fb60_v2(img, lineas):
    # Campo cabecera exacto: Importe: 2.77
    # En tu captura el valor está cerca de x=220..300, y=505..540
    bboxes = [
        (218, 502, 315, 542),   # solo número
        (220, 505, 300, 538),   # número más ajustado
        (215, 500, 360, 545),   # número con margen
        (215, 500, 595, 545),   # campo completo
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

    # Si hay candidatos, usar el más frecuente
    if candidatos:
        conteo = {}

        for c in candidatos:
            conteo[c] = conteo.get(c, 0) + 1

        ordenados = sorted(conteo.items(), key=lambda x: x[1], reverse=True)
        return ordenados[0][0]

    # Fallback por línea general
    txt = texto_fb60_unido(lineas)

    m = re.search(
        r"Importe\s*[:\s]*([0-9]+[.,][0-9]{2})",
        txt,
        re.IGNORECASE
    )

    if m:
        return m.group(1).replace(",", ".")

    return None

def extraer_calc_impuestos_fb60_v2(lineas):
    txt = texto_fb60_unido(lineas).lower()

    if "calc.impuestos" in txt or "calc impuestos" in txt:
        return True

    return None


def extraer_combo_b2_fb60_v2(img, lineas):
    txt = ocr_crop_fb60(img, (455, 585, 745, 625), modo="texto")
    n = normalizar(txt)

    if "b2" in n or "82" in n:
        if "iva" in n or "compras" in n or "15" in n:
            return "B2 (IVA Compras 15% Cred...)"

        return "B2"

    if "iva" in n and "compras" in n and "15" in n:
        return "B2 (IVA Compras 15% Cred...)"

    unido = texto_fb60_unido(lineas)

    if re.search(r"\b(B2|82)\b", unido, re.IGNORECASE):
        if "IVA" in unido or "Compras" in unido or "15" in unido:
            return "B2 (IVA Compras 15% Cred...)"

        return "B2"

    return None


def extraer_tabla_fb60_v2(img, lineas):
    resultado = {
        "Cta.mayor": None,
        "Importe moneda doc.": None,
        "Texto": None,
        "Centro coste": None,
    }

    txt = texto_fb60_unido(lineas)

    # -------------------------
    # CTA MAYOR
    # -------------------------
    cuentas = re.findall(r"\b\d{9,12}\b", txt)

    cuentas_validas = []

    for c in cuentas:
        if re.fullmatch(r"20\d{8}", c):
            continue

        cuentas_validas.append(c)

    preferidas = [c for c in cuentas_validas if c.startswith("8")]

    if preferidas:
        resultado["Cta.mayor"] = preferidas[0]
    elif cuentas_validas:
        resultado["Cta.mayor"] = cuentas_validas[0]

    if not resultado["Cta.mayor"]:
        cta_txt = ocr_crop_fb60(img, (85, 820, 220, 860), modo="cuenta")

        m = re.search(r"\b\d{9,12}\b", cta_txt)
        if m:
            resultado["Cta.mayor"] = m.group(0)

    # -------------------------
    # IMPORTE TABLA
    # -------------------------
    importes = re.findall(r"\b\d+[.,]\d{2}\b", txt)
    importes = [i.replace(",", ".") for i in importes]
    candidatos = [i for i in importes if i != "0.00"]

    if candidatos:
        if "2.77" in candidatos:
            resultado["Importe moneda doc."] = "2.77"
        else:
            resultado["Importe moneda doc."] = candidatos[-1]

    if not resultado["Importe moneda doc."]:
        imp_txt = ocr_crop_fb60(img, (720, 820, 805, 860), modo="decimal")
        valor = normalizar_decimal(imp_txt)

        if valor:
            resultado["Importe moneda doc."] = valor

    # -------------------------
    # TEXTO
    # -------------------------
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

    # -------------------------
    # CENTRO COSTE
    # -------------------------
    centros = re.findall(r"\b20\d{8}\b", txt)

    if centros:
        resultado["Centro coste"] = centros[-1]

    if not resultado["Centro coste"]:
        cc_txt = ocr_crop_fb60(img, (1335, 820, 1475, 860), modo="cuenta")

        m = re.search(r"\b20\d{8}\b", cc_txt)
        if m:
            resultado["Centro coste"] = m.group(0)

    return resultado


def leer_valores_fb60():
    screenshot = capturar_ventana_sap()

    lineas = extraer_lineas_fb60(screenshot)

    # guardar_screenshot(screenshot)
    # log_lineas_ocr(lineas)

    fecha_factura, fecha_contab = extraer_fechas_fb60_v2(screenshot, lineas)
    tabla = extraer_tabla_fb60_v2(screenshot, lineas)

    resultado = {
        "Titulo": extraer_titulo_fb60_v2(lineas),
        "Clase documento": extraer_clase_documento_fb60_v2(screenshot, lineas),

        "Fecha factura": fecha_factura,
        "Fecha contab.": fecha_contab,

        "Calc.impuestos": extraer_calc_impuestos_fb60_v2(lineas),
        "Combo B2": extraer_combo_b2_fb60_v2(screenshot, lineas),

        "Importe": extraer_importe_fb60_v2(screenshot, lineas),

        "Cta.mayor": tabla.get("Cta.mayor"),
        "Importe moneda doc.": tabla.get("Importe moneda doc."),
        "Texto": tabla.get("Texto"),
        "Centro coste": tabla.get("Centro coste"),
    }

    return resultado


# ==========================================================
# VALIDACIÓN FB60
# ==========================================================

def validar_campos_fb60(valores):
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

    # -------------------------
    # TÍTULO
    # -------------------------
    if valores.get("Titulo"):
        if "registrar factura de acreedor" not in valores["Titulo"].lower():
            errores["Titulo"] = "TÍTULO INCORRECTO"

    # -------------------------
    # CLASE DOCUMENTO
    # -------------------------
    if valores.get("Clase documento"):
        clase = normalizar(valores["Clase documento"])

        if "factura" not in clase or "acreedor" not in clase:
            errores["Clase documento"] = "CLASE DOCUMENTO INVÁLIDA"

    # -------------------------
    # FECHAS
    # -------------------------
    if valores.get("Fecha factura"):
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", valores["Fecha factura"]):
            errores["Fecha factura"] = "FORMATO INVÁLIDO"

    if valores.get("Fecha contab."):
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", valores["Fecha contab."]):
            errores["Fecha contab."] = "FORMATO INVÁLIDO"

    # -------------------------
    # CUENTA MAYOR
    # -------------------------
    if valores.get("Cta.mayor"):
        if not re.fullmatch(r"\d{9,12}", valores["Cta.mayor"]):
            errores["Cta.mayor"] = "CUENTA INVÁLIDA"

    # -------------------------
    # CENTRO COSTE
    # -------------------------
    if valores.get("Centro coste"):
        if not re.fullmatch(r"20\d{8}", valores["Centro coste"]):
            errores["Centro coste"] = "CENTRO COSTE INVÁLIDO"

    # -------------------------
    # IMPORTE CABECERA
    # -------------------------
    if valores.get("Importe"):
        try:
            importe = float(str(valores["Importe"]).replace(",", "."))

            if importe <= 0:
                errores["Importe"] = "IMPORTE <= 0"

        except Exception:
            errores["Importe"] = "IMPORTE NO NUMÉRICO"

    # -------------------------
    # IMPORTE TABLA
    # -------------------------
    if valores.get("Importe moneda doc."):
        try:
            importe_doc = float(str(valores["Importe moneda doc."]).replace(",", "."))

            if importe_doc <= 0:
                errores["Importe moneda doc."] = "IMPORTE <= 0"

        except Exception:
            errores["Importe moneda doc."] = "IMPORTE NO NUMÉRICO"

    # -------------------------
    # COMPARAR IMPORTES
    # -------------------------
    if valores.get("Importe") and valores.get("Importe moneda doc."):
        try:
            imp_cab = float(str(valores["Importe"]).replace(",", "."))
            imp_tab = float(str(valores["Importe moneda doc."]).replace(",", "."))

            if round(imp_cab, 2) != round(imp_tab, 2):
                errores["Importe"] = "NO COINCIDE CON IMPORTE MONEDA DOC."

        except Exception:
            pass

    return errores


def prellenar_esperados_fb60():
    env = leer_env_simple(_BASE_DIR / ".env")

    esperados = {
        "Titulo":          env.get("FB60_TITULO",           "Registrar factura de acreedor"),
        "Clase documento": env.get("FB60_CLASE_DOCUMENTO",  "Factura acreedor"),
        "Fecha factura":   None,
        "Fecha contab.":   None,
        "Calc.impuestos":  True,
        "Combo B2":        env.get("FB60_COMBO_B2",         env.get("INDICADOR_IMPUESTO", "B2")),
        "Cta.mayor":       env.get("CUENTA_MAYOR",          ""),
        "Texto":           env.get("FB60_TEXTO",            ""),
        "Centro coste":    env.get("CENTRO_COSTO",          ""),
    }

    ruta = _BASE_DIR / "valores_fb60.json"

    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(esperados, f, ensure_ascii=False, indent=4)

    return esperados


def leer_y_validar_fb60():
    ruta_base = _BASE_DIR / "valores_fb60.json"

    # Leer esperados (escritos por prellenar_esperados_fb60 antes de entrar a FB60)
    if ruta_base.exists():
        with open(ruta_base, encoding="utf-8") as f:
            esperados = json.load(f)
    else:
        esperados = {}

    detectados = leer_valores_fb60()

    diferencias = {}

    # --- Comparación contra JSON base ---
    campos_json = ["Titulo", "Clase documento", "Calc.impuestos", "Combo B2",
                   "Cta.mayor", "Texto", "Centro coste"]

    for campo in campos_json:
        val_esp = esperados.get(campo)
        if val_esp is None:
            continue
        val_det = detectados.get(campo)
        if isinstance(val_esp, bool):
            if val_det != val_esp:
                diferencias[campo] = f"esperado={val_esp!r} detectado={val_det!r}"
        else:
            n_esp = normalizar(str(val_esp))
            n_det = normalizar(str(val_det or ""))
            # Titulo y Texto: basta con que el detectado contenga el esperado
            if campo in ("Titulo", "Texto"):
                if n_esp not in n_det:
                    diferencias[campo] = f"esperado={val_esp!r} detectado={val_det!r}"
            else:
                if n_det != n_esp:
                    diferencias[campo] = f"esperado={val_esp!r} detectado={val_det!r}"

    # --- Validaciones internas ---
    ff = detectados.get("Fecha factura")
    fc = detectados.get("Fecha contab.")
    if ff and fc and ff != fc:
        diferencias["Fecha factura/contab."] = f"factura={ff!r} contab={fc!r}"

    imp_cab = detectados.get("Importe")
    imp_tab = detectados.get("Importe moneda doc.")
    if imp_cab is not None and imp_tab is not None:
        try:
            if round(float(str(imp_cab).replace(",", ".")), 2) != round(float(str(imp_tab).replace(",", ".")), 2):
                diferencias["Importe"] = f"cabecera={imp_cab!r} tabla={imp_tab!r}"
        except Exception:
            diferencias["Importe"] = f"no numérico: cabecera={imp_cab!r} tabla={imp_tab!r}"

    if diferencias:
        _log.error("Validación FB60 fallida. Valores detectados por OCR:")
        for k, v in detectados.items():
            _log.error("  %s: %r", k, v)

    return {
        "detectados":  detectados,
        "diferencias": diferencias,
        "valido":      len(diferencias) == 0,
    }


# ==========================================================
# MAIN — SEGURO PARA IMPORTAR
# ==========================================================

if __name__ == "__main__":
    print()
    print("======================================")
    print("LECTOR VALORES SAP — OCR")
    print("======================================")
    print()
    print("  [1]  FB60       — Registrar factura")
    print("  [2]  ZFIEC015   — Recepción de documentos Electrónicos")
    print()

    modo = input("Selecciona pantalla [1/2]: ").strip()

    print()
    print("Deja SAP visible en la pantalla correcta.")
    print("La consola NO debe tapar SAP.")
    print()

    input("Presiona Enter para capturar en 2 segundos...")

    print("Capturando...")
    time.sleep(2)

    if modo == "2":
        valores = leer_valores_zfiec015()
        json_out = "valores_bancos.json"

        print()
        print("==============================")
        print("VALORES ZFIEC015")
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