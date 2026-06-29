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

from PIL import ImageGrab, ImageOps, ImageEnhance
import pytesseract


# ==========================================================
# CONFIGURACIÓN
# ==========================================================

_BASE_DIR = pathlib.Path(__file__).parent.parent


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
    input("Enter para salir...")
    sys.exit(1)

pytesseract.pytesseract.tesseract_cmd = tesseract_path


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
    txt = txt.replace("_", "")
    txt = txt.replace("‘", "")
    txt = txt.replace("’", "")
    txt = txt.replace("“", "")
    txt = txt.replace("”", "")
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
    return txt


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
# CAPTURA SAP
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
# ZFIEC015
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

            for nx, ny in (
                (xx + 1, yy),
                (xx - 1, yy),
                (xx, yy + 1),
                (xx, yy - 1),
                (xx + 1, yy + 1),
                (xx - 1, yy - 1),
                (xx + 1, yy - 1),
                (xx - 1, yy + 1),
            ):
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

    for nombre, texto in opciones:
        t = texto.lower().replace(" ", "")

        if "(e)" in t:
            return nombre

        if "(@)" in t:
            return nombre

    for nombre, texto in opciones:
        t = texto.lower().replace(" ", "")

        if nombre == "Rechazo" and "@" in t:
            return nombre

        if "(e" in t or "e)" in t:
            return nombre

    return None


def leer_valores_zfiec015():
    screenshot, palabras, lineas = obtener_palabras_lineas_desde_pantalla()

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

    ruta_json = pathlib.Path(__file__).parent / "valores_bancos.json"
    with open(ruta_json, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=4)

    return resultado


# ==========================================================
# FB60
# ==========================================================

def extraer_fechas_fb60(lineas):
    fecha_factura = None
    fecha_contab = None
    patron_fecha = r"(\d{2}\.\d{2}\.\d{4})"

    for linea in lineas:
        t = linea["texto"]

        if "Fecha factura" in t:
            m = re.search(patron_fecha, t)
            if m:
                fecha_factura = m.group(1)

        if "Fecha contab" in t:
            m = re.search(patron_fecha, t)
            if m:
                fecha_contab = m.group(1)

    return fecha_factura, fecha_contab


def extraer_calc_impuestos_fb60(palabras):
    hay_calc = any("calc" in p["norm"] for p in palabras)
    hay_impuestos = any("impuestos" in p["norm"] for p in palabras)
    return True if hay_calc and hay_impuestos else None


def extraer_combo_b2_fb60(palabras):
    candidatos_b2 = [
        p for p in palabras
        if p["norm"] in ("b2", "82") or p["texto"].upper() == "B2"
    ]

    if not candidatos_b2:
        return None

    mejores = []

    for b2 in candidatos_b2:
        y_ref = b2["cy"]
        misma_linea = []

        for p in palabras:
            if abs(p["cy"] - y_ref) <= 12 and b2["left"] - 8 <= p["left"] <= b2["left"] + 390:
                misma_linea.append(p)

        misma_linea.sort(key=lambda x: x["left"])

        texto = limpiar(" ".join(p["texto"] for p in misma_linea))
        texto = re.sub(r"\s+[vV]$", "", texto).strip()

        score = 0
        t = texto.lower()

        if "b2" in t:
            score += 100
        if "iva" in t:
            score += 100
        if "compras" in t or "compra" in t:
            score += 100
        if "15" in t:
            score += 80
        if "cred" in t:
            score += 80

        mejores.append((score, texto))

    mejores.sort(reverse=True, key=lambda x: x[0])

    return mejores[0][1] if mejores and mejores[0][0] > 0 else None


def extraer_tabla_fb60(lineas):
    resultado = {
        "Cta.mayor": None,
        "Importe moneda doc.": None,
        "Texto": None,
        "Centro coste": None,
    }

    posibles = []

    for linea in lineas:
        t = linea["texto"]

        score = 0

        if re.search(r"\b\d{9,12}\b", t):
            score += 100

        if "Debe" in t:
            score += 60

        if re.search(r"\b\d+\.\d{2}\b", t):
            score += 60

        if "COMISION" in t.upper():
            score += 80

        if "2047001103" in t:
            score += 100

        if score > 0:
            posibles.append((score, t))

    posibles.sort(reverse=True, key=lambda x: x[0])

    if not posibles:
        return resultado

    texto = posibles[0][1]

    m = re.search(r"\b(\d{9,12})\b", texto)
    if m:
        resultado["Cta.mayor"] = m.group(1)

    importes = re.findall(r"\b\d+\.\d{2}\b", texto)
    if importes:
        resultado["Importe moneda doc."] = importes[-1]

    m = re.search(r"\b(20\d{8})\b", texto)
    if m:
        resultado["Centro coste"] = m.group(1)

    m = re.search(
        r"(COMISION\s+[A-ZÁÉÍÓÚÑ0-9. ]+?)(?:\s+2000|\s+20\d{8}|$)",
        texto,
        re.IGNORECASE
    )

    if m:
        resultado["Texto"] = limpiar(m.group(1))

    return resultado


def leer_valores_fb60():
    screenshot, palabras, lineas = obtener_palabras_lineas_desde_pantalla()

    fecha_factura, fecha_contab = extraer_fechas_fb60(lineas)
    tabla = extraer_tabla_fb60(lineas)

    resultado = {
        "Fecha factura": fecha_factura,
        "Fecha contab.": fecha_contab,
        "Calc.impuestos": extraer_calc_impuestos_fb60(palabras),
        "Combo B2": extraer_combo_b2_fb60(palabras),
        "Cta.mayor": tabla.get("Cta.mayor"),
        "Importe moneda doc.": tabla.get("Importe moneda doc."),
        "Texto": tabla.get("Texto"),
        "Centro coste": tabla.get("Centro coste"),
    }

    with open("valores_sap.json", "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=4)

    return resultado


# ==========================================================
# MAIN
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
    print("La consola NO debe tapar los campos.")
    print()

    input("Presiona Enter para capturar en 2 segundos...")

    print("Capturando...")
    time.sleep(2)

    if modo == "2":
        valores = leer_valores_zfiec015()
        json_out = "valores_bancos.json"
    else:
        valores = leer_valores_fb60()
        json_out = "valores_sap.json"

    print()
    print("==============================")
    print("VALORES DETECTADOS")
    print("==============================")

    for k, v in valores.items():
        print(f"  {k}: {repr(v)}")

    print()
    print(f"JSON: {json_out}")
    print()

    input("Enter para salir...")