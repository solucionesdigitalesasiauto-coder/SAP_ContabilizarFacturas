# -*- coding: utf-8 -*-
"""
============================================================================
LECTOR / VALIDADOR DE VALORES SAP POR OCR — VERSIÓN OPTIMIZADA
============================================================================

Refactorización de validacion_Pantalla.py con dos capas de mejora:

MEJORA 1 — OCR MEJORADO (módulos marcados [M1]):
    * Pipeline de preprocesamiento configurable: escala de grises,
      umbral adaptativo, reducción de ruido (mediano/gaussiano),
      CLAHE / autocontraste, sharpening, inversión de colores.
    * Motor OCR multi-backend: Tesseract (primario) + EasyOCR /
      PaddleOCR opcionales como segunda opinión si están instalados.
    * Validación de confianza: umbral mínimo configurable; si el
      reconocimiento no alcanza la confianza requerida se activan
      reintentos con variantes de preprocesamiento alternativas
      (escalera de escalado / binarización / inversión / sharpening).

MEJORA 2 — RENDIMIENTO (módulos marcados [M2]):
    * Captura restringida a la región de interés (ROI) del .env o
      CONFIG en vez de pantalla completa.
    * Backend de captura de bajo nivel `mss` (5-20x más rápido que
      PIL.ImageGrab) con fallback automático a ImageGrab.
    * Captura diferencial: hash perceptual de la pantalla; si no hay
      cambios desde el último OCR se reutiliza el resultado cacheado.
    * OCR de crops en PARALELO (ThreadPoolExecutor — Tesseract corre
      en subprocesos, por lo que los hilos escalan casi linealmente).
    * SALIDA TEMPRANA en los bucles variante×config: en el código
      original cada campo numérico ejecutaba hasta 12 llamadas a
      Tesseract aunque la primera ya fuera válida. Ahora se corta en
      cuanto hay resultado confiable/consistente. (Principal causa de
      los 12-20 s originales.)
    * Cache de bboxes escalados y de posiciones de labels por
      resolución de pantalla (evita recalcular coordenadas).
    * Análisis de píxeles con numpy si está disponible (checkbox,
      detección de inputs) en lugar de getpixel() punto a punto.
    * Resolución de re-escalado para OCR reducida y configurable
      (fullpage 2x -> configurable; crops 8x -> 4-6x configurable).

FUNCIONALIDAD CONSERVADA:
    * Misma lógica de validación esperado vs. detectado (FB60,
      ZFIEC015, FB60 Detalle), mismos criterios de éxito/fracaso,
      mismos archivos JSON de entrada/salida y mismo formato de
      reporte por consola / logging.
    * Mismos puntos de entrada públicos:
        leer_y_validar_fb60(), leer_y_validar_zfiec015(),
        leer_y_validar_fb60_detalle(), leer_valores_fb60(), etc.
    * Mismo comportamiento en entorno congelado (PyInstaller) y misma
      búsqueda de tesseract.exe.

DEPENDENCIAS:
    Obligatorias:  Pillow, pytesseract  (idénticas al original)
    Opcionales:    mss        -> captura rápida        (pip install mss)
                   opencv-python + numpy -> preprocesado avanzado
                   easyocr    -> backend OCR alternativo
    El código degrada limpiamente a PIL puro si faltan las opcionales.

MÉTRICAS: ejecutar con  `python validacion_pantalla_optimizado.py --benchmark`
para medir tiempos reales en tu equipo (ver sección MÉTRICAS al final).
============================================================================
"""

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
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image, ImageGrab, ImageOps, ImageEnhance, ImageFilter
import pytesseract

_log = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Dependencias opcionales (degradación limpia si no están instaladas)
# ----------------------------------------------------------------------
try:                                    # [M2] captura de bajo nivel
    import mss
    _HAS_MSS = True
except ImportError:
    _HAS_MSS = False

try:                                    # [M1]/[M2] preprocesado avanzado
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    import cv2
    _HAS_CV2 = _HAS_NUMPY               # cv2 requiere numpy
except ImportError:
    _HAS_CV2 = False

_EASYOCR_READER = None                  # [M1] singleton perezoso EasyOCR


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
# [M1]+[M2] CONFIG — parámetros ajustables para calibración
# ------------------------------------------------------------
# Todos los valores pueden sobreescribirse creando un archivo
# `config_ocr.json` junto al ejecutable, con las mismas claves.
# ============================================================

CONFIG = {
    # ---------- [M2] Captura ----------
    "usar_mss": True,               # backend rápido mss si está instalado
    "region_captura": None,         # (x, y, ancho, alto) o None -> .env / full
    "captura_diferencial": True,    # saltar OCR si la pantalla no cambió
    "dif_umbral": 0.004,            # fracción de píxeles distintos p/ "cambio"
    "dif_hash_lado": 64,            # lado del thumbnail para el hash de cambio
    "cache_ttl_seg": 30,            # vida máxima del cache OCR de pantalla

    # ---------- [M2] Paralelismo ----------
    "ocr_workers": 4,               # hilos para OCR de crops en paralelo
    "salida_temprana": True,        # cortar bucles variante×config al acertar

    # ---------- [M1]/[M2] Preprocesamiento ----------
    "fullpage_escala": 2.0,         # re-escalado del OCR de página completa
    "crop_escala": 3,               # re-escalado base de crops — a 5x+ Tesseract
                                     # confundía '8' con '9' en importes (ej.
                                     # '4.08'->'4.09'), confirmado 06/07/2026
                                     # probando escalas 1-8 sobre el mismo crop
    "crop_escala_max": 5,           # escalado máx. en reintentos (antes 8, mismo bug)
    "contraste": 2.6,               # factor de contraste PIL
    "usar_umbral_adaptativo": False, # adaptive threshold (cv2) — desactivado:
                                     # deformaba dígitos finos ('2'->'7'),
                                     # el original nunca lo usó (06/07/2026)
    "adapt_block": 31,              # tamaño de bloque umbral adaptativo (impar)
    "adapt_C": 10,                  # constante C del umbral adaptativo
    "denoise": "off",               # "mediana" | "gauss" | "off" — el median
                                     # blur borraba trazos finos en crops
                                     # pequeños (mismo bug, 06/07/2026)
    "denoise_kernel": 3,            # kernel del filtro de ruido (impar)
    "clahe_clip": 2.0,              # clip limit CLAHE (cv2)

    # ---------- [M1] Confianza / reintentos ----------
    "conf_min_palabra": 10,         # conf mínima por palabra (igual original)
    "conf_min_campo": 55,           # conf media mínima para aceptar un crop
    "max_reintentos_crop": 4,       # variantes extra si conf < conf_min_campo
                                     # (subido a 4 para no perder la variante
                                     # "invertir" al agregar la variante 0
                                     # sin escalar — ver variantes_reintento)
    "backend_secundario": "auto",   # "easyocr" | "off" | "auto" (si instalado)

    # ---------- FB60 (idéntico al original) ----------
    "fb60_base_w": 1580,
    "fb60_base_h": 1080,
}


def _cargar_config_externa():
    """Fusiona config_ocr.json (si existe) sobre CONFIG. [M1]/[M2]"""
    ruta = _BASE_DIR / "config_ocr.json"
    if not ruta.exists():
        return
    try:
        with open(ruta, encoding="utf-8") as f:
            externo = json.load(f)
        for k, v in externo.items():
            if k in CONFIG:
                CONFIG[k] = v
        _log.info("config_ocr.json aplicado: %s claves", len(externo))
    except Exception as e:
        _log.warning("config_ocr.json inválido, se ignora: %s", e)


_cargar_config_externa()


# ============================================================
# [M2] MÉTRICAS DE TIEMPO DE EJECUCIÓN
# ============================================================

class Metricas:
    """Acumulador thread-safe de tiempos por etapa."""

    def __init__(self):
        self._lock = threading.Lock()
        self.datos = {}          # etapa -> [total_seg, n_llamadas]
        self.t_inicio = None

    def reset(self):
        with self._lock:
            self.datos = {}
            self.t_inicio = time.perf_counter()

    def registrar(self, etapa, segundos):
        with self._lock:
            acc = self.datos.setdefault(etapa, [0.0, 0])
            acc[0] += segundos
            acc[1] += 1

    def total(self):
        return (time.perf_counter() - self.t_inicio) if self.t_inicio else 0.0

    def reporte(self):
        lineas = ["", "----- MÉTRICAS DE TIEMPO -----"]
        with self._lock:
            for etapa, (tot, n) in sorted(
                self.datos.items(), key=lambda x: -x[1][0]
            ):
                lineas.append(
                    f"  {etapa:<32} {tot:7.2f} s  ({n} llamadas)"
                )
        lineas.append(f"  {'TOTAL PROCESO':<32} {self.total():7.2f} s")
        lineas.append("------------------------------")
        return "\n".join(lineas)


METRICAS = Metricas()


class cronometro:
    """Context manager: with cronometro('etapa'): ..."""

    def __init__(self, etapa):
        self.etapa = etapa

    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        METRICAS.registrar(self.etapa, time.perf_counter() - self.t0)
        return False


# ============================================================
# TESSERACT (idéntico al original)
# ============================================================

def buscar_tesseract():
    """Localiza tesseract.exe en rutas estándar de Windows, PATH o glob."""
    rutas = [
        str(_BASE_DIR / "Tesseract-OCR" / "tesseract.exe"),
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
        pytesseract.pytesseract.tesseract_cmd = _tess_sistema
        os.environ["TESSDATA_PREFIX"] = os.path.join(
            os.path.dirname(_tess_sistema), "tessdata"
        )
    else:
        pytesseract.pytesseract.tesseract_cmd = os.path.join(
            sys._MEIPASS, "tesseract.exe"
        )
        os.environ["TESSDATA_PREFIX"] = os.path.join(sys._MEIPASS, "tessdata")
else:
    tesseract_path = buscar_tesseract()
    if not tesseract_path:
        print("ERROR: No se encontró tesseract.exe")
        sys.exit(1)
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
    os.environ["TESSDATA_PREFIX"] = os.path.join(
        os.path.dirname(tesseract_path), "tessdata"
    )


def verificar_tesseract() -> bool:
    """Ejecuta un OCR mínimo para confirmar que Tesseract está operativo."""
    try:
        img = Image.new("RGB", (120, 30), color=(255, 255, 255))
        pytesseract.image_to_string(img, lang="eng", config="--psm 6")
        return True
    except Exception as e:
        _log.error("Tesseract no operativo: %s", e)
        return False


# ============================================================
# FUNCIONES GENERALES OCR / NORMALIZACIÓN (idénticas al original)
# ============================================================

def _nd(v):
    """Formatea un valor OCR: None → 'N/D' (no detectado)."""
    return "N/D" if v is None else repr(v)


def limpiar(txt):
    """Limpia texto OCR: saltos de línea, símbolos y espacios extra."""
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
    txt = txt.replace("\u2018", "")
    txt = txt.replace("\u2019", "")
    txt = txt.replace("\u201c", "")
    txt = txt.replace("\u201d", "")
    txt = txt.replace("\u2014", " ")
    txt = txt.replace("\u2026", "...")
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
    for ch in (" ", ".", ":", ";", "*", ",", "/", "-", "(", ")"):
        txt = txt.replace(ch, "")
    return txt


def limpiar_texto_ocr_fuerte(txt):
    """Limpieza agresiva para bordes/prefijos OCR."""
    txt = limpiar(txt)
    txt = re.sub(r"^\*+\s*", "", txt)
    txt = re.sub(r"^SAP\s+", "", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


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


# ============================================================
# [M2] CAPTURA DE PANTALLA RÁPIDA + DIFERENCIAL
# ------------------------------------------------------------
# 1. Región: CONFIG["region_captura"] > .env (SAP_WIN_*) > full.
# 2. Backend: mss (bajo nivel, sin copia GDI completa) con
#    fallback transparente a PIL.ImageGrab.
# 3. Diferencial: thumbnail en gris de 64x64; si la diferencia
#    media contra la última captura procesada es menor al umbral,
#    se reutiliza el resultado OCR cacheado (evita procesamiento
#    continuo innecesario cuando la UI no cambió).
# ============================================================

_MSS_LOCAL = threading.local()          # mss no es thread-safe -> 1 por hilo


def _region_configurada():
    """Devuelve (x, y, w, h) desde CONFIG o .env, o None para full screen."""
    if CONFIG.get("region_captura"):
        try:
            x, y, w, h = [int(v) for v in CONFIG["region_captura"]]
            if w > 0 and h > 0:
                return x, y, w, h
        except Exception as e:
            _log.warning("region_captura inválida en CONFIG: %s", e)

    env = leer_env_simple(_BASE_DIR / ".env")
    try:
        x = int(env.get("SAP_WIN_X", 0))
        y = int(env.get("SAP_WIN_Y", 0))
        w = int(env.get("SAP_WIN_ANCHO", 0))
        h = int(env.get("SAP_WIN_ALTO", 0))
        if w > 0 and h > 0:
            return x, y, w, h
    except Exception:
        pass

    return None


def _grab_mss(region):
    """Captura con mss. region=(x,y,w,h) o None."""
    sct = getattr(_MSS_LOCAL, "sct", None)
    if sct is None:
        sct = mss.mss()
        _MSS_LOCAL.sct = sct

    if region:
        x, y, w, h = region
        mon = {"left": x, "top": y, "width": w, "height": h}
    else:
        mon = sct.monitors[1]           # monitor primario completo

    raw = sct.grab(mon)
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def capturar_ventana_sap():
    """
    Captura SAP usando la región configurada (.env / CONFIG).

    [M2] Usa mss si está disponible (5-20x más rápido que ImageGrab);
    fallback automático a PIL.ImageGrab ante cualquier fallo.
    """
    region = _region_configurada()

    with cronometro("captura_pantalla"):
        if _HAS_MSS and CONFIG.get("usar_mss", True):
            try:
                return _grab_mss(region)
            except Exception as e:
                _log.warning("mss falló (%s); fallback a ImageGrab", e)

        try:
            if region:
                x, y, w, h = region
                return ImageGrab.grab(bbox=(x, y, x + w, y + h))
            return ImageGrab.grab()
        except Exception as e:
            raise RuntimeError(
                f"No se pudo capturar la pantalla: {e}"
            ) from e


# ---------- Captura diferencial / cache de OCR de pantalla ----------

class _CacheOCRPantalla:
    """
    [M2] Guarda el último (screenshot, palabras, lineas) junto con un
    hash perceptual. Si la nueva captura es 'igual' (< dif_umbral) y
    el cache no expiró, se reutiliza sin volver a ejecutar Tesseract.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._firma = None
        self._resultado = None
        self._timestamp = 0.0

    @staticmethod
    def _firma_imagen(img):
        lado = int(CONFIG.get("dif_hash_lado", 64))
        thumb = img.convert("L").resize((lado, lado))
        if _HAS_NUMPY:
            return np.asarray(thumb, dtype=np.int16)
        return list(thumb.getdata())

    @staticmethod
    def _fraccion_distinta(f1, f2):
        if f1 is None or f2 is None:
            return 1.0
        if _HAS_NUMPY:
            return float((np.abs(f1 - f2) > 12).mean())
        distintos = sum(1 for a, b in zip(f1, f2) if abs(a - b) > 12)
        return distintos / max(1, len(f1))

    def hay_cambio(self, img):
        """True si la imagen difiere de la última procesada."""
        firma = self._firma_imagen(img)
        with self._lock:
            expirado = (
                time.monotonic() - self._timestamp
                > CONFIG.get("cache_ttl_seg", 30)
            )
            cambio = (
                expirado
                or self._resultado is None
                or self._fraccion_distinta(firma, self._firma)
                > CONFIG.get("dif_umbral", 0.004)
            )
        return cambio, firma

    def guardar(self, firma, resultado):
        with self._lock:
            self._firma = firma
            self._resultado = resultado
            self._timestamp = time.monotonic()

    def obtener(self):
        with self._lock:
            return self._resultado

    def invalidar(self):
        with self._lock:
            self._firma = None
            self._resultado = None


_CACHE_PANTALLA = _CacheOCRPantalla()


class MonitorCambios:
    """
    [M2] Utilidad para uso continuo (loops de validación): captura en un
    hilo separado y dispara el callback SOLO cuando la interfaz cambió.

        mon = MonitorCambios(intervalo=0.5)
        mon.iniciar(lambda img: print("UI actualizada"))
        ...
        mon.detener()
    """

    def __init__(self, intervalo=0.5):
        self.intervalo = intervalo
        self._stop = threading.Event()
        self._hilo = None
        self._cache = _CacheOCRPantalla()

    def iniciar(self, callback):
        def _loop():
            while not self._stop.is_set():
                try:
                    img = capturar_ventana_sap()
                    cambio, firma = self._cache.hay_cambio(img)
                    if cambio:
                        self._cache.guardar(firma, True)
                        callback(img)
                except Exception as e:
                    _log.warning("MonitorCambios: %s", e)
                self._stop.wait(self.intervalo)

        self._stop.clear()
        self._hilo = threading.Thread(target=_loop, daemon=True)
        self._hilo.start()

    def detener(self):
        self._stop.set()
        if self._hilo:
            self._hilo.join(timeout=2)


# ============================================================
# [M1] PREPROCESAMIENTO DE IMAGEN PARA OCR
# ------------------------------------------------------------
# Pipeline: gris -> denoise -> contraste/CLAHE -> umbral
# adaptativo -> (escala). Con OpenCV usa adaptiveThreshold +
# medianBlur/GaussianBlur + CLAHE; sin OpenCV degrada a un
# pipeline PIL equivalente (MedianFilter/GaussianBlur,
# autocontrast y binarización global).
# ============================================================

def _pil_a_cv(img):
    return cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2GRAY)


def _cv_a_pil(mat):
    return Image.fromarray(mat)


def preprocesar_imagen(img, escala=None, invertir=False,
                       binarizar=None, sharpen=0.0):
    """
    [M1] Preprocesa una imagen (o crop) para OCR.

    Args:
        img:       PIL.Image de entrada (color o gris).
        escala:    factor de re-escalado (None -> CONFIG['crop_escala']).
        invertir:  invierte colores (texto claro sobre fondo oscuro).
        binarizar: True/False fuerza binarización; None usa CONFIG.
        sharpen:   factor extra de nitidez (0 = desactivado).

    Returns:
        PIL.Image en modo 'L' lista para Tesseract.
    """
    if escala is None:
        escala = CONFIG.get("crop_escala", 5)
    if binarizar is None:
        binarizar = CONFIG.get("usar_umbral_adaptativo", True)

    with cronometro("preprocesamiento"):
        try:
            if _HAS_CV2:
                gris = _pil_a_cv(img)

                # --- reducción de ruido ---
                k = max(3, int(CONFIG.get("denoise_kernel", 3)) | 1)
                modo = CONFIG.get("denoise", "mediana")
                if modo == "mediana":
                    gris = cv2.medianBlur(gris, k)
                elif modo == "gauss":
                    gris = cv2.GaussianBlur(gris, (k, k), 0)

                # --- contraste ---
                # CLAHE deformaba dígitos finos en ciertas escalas (ej. '8'
                # leído como '9' o '6') — confirmado 06/07/2026. Por defecto
                # usa autocontraste simple (igual al original probado en
                # producción); CLAHE queda opcional vía config_ocr.json.
                if CONFIG.get("usar_clahe", False):
                    clahe = cv2.createCLAHE(
                        clipLimit=float(CONFIG.get("clahe_clip", 2.0)),
                        tileGridSize=(8, 8),
                    )
                    gris = clahe.apply(gris)
                else:
                    gris_pil = ImageOps.autocontrast(Image.fromarray(gris))
                    gris_pil = ImageEnhance.Contrast(gris_pil).enhance(
                        CONFIG.get("contraste", 2.6)
                    )
                    gris = np.asarray(gris_pil)

                # --- escalado (antes del umbral: mejora bordes) ---
                if escala and escala != 1:
                    gris = cv2.resize(
                        gris, None, fx=escala, fy=escala,
                        interpolation=cv2.INTER_CUBIC,
                    )

                # --- umbral adaptativo ---
                if binarizar:
                    block = max(3, int(CONFIG.get("adapt_block", 31)) | 1)
                    gris = cv2.adaptiveThreshold(
                        gris, 255,
                        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                        cv2.THRESH_BINARY,
                        block,
                        int(CONFIG.get("adapt_C", 10)),
                    )

                if invertir:
                    gris = 255 - gris

                out = _cv_a_pil(gris)

            else:
                # ---------- Fallback PIL puro ----------
                out = img.convert("L")

                modo = CONFIG.get("denoise", "mediana")
                k = max(3, int(CONFIG.get("denoise_kernel", 3)) | 1)
                if modo == "mediana":
                    out = out.filter(ImageFilter.MedianFilter(k))
                elif modo == "gauss":
                    out = out.filter(ImageFilter.GaussianBlur(radius=1))

                if escala and escala != 1:
                    out = out.resize(
                        (int(out.width * escala), int(out.height * escala)),
                        Image.LANCZOS,
                    )

                out = ImageOps.autocontrast(out)
                out = ImageEnhance.Contrast(out).enhance(
                    CONFIG.get("contraste", 2.6)
                )

                if binarizar:
                    # binarización Otsu simplificada (media del histograma)
                    hist = out.histogram()
                    total = sum(hist)
                    acum, umbral = 0, 128
                    for i, hcount in enumerate(hist):
                        acum += i * hcount
                    media = acum / max(1, total)
                    umbral = int(media * 0.9)
                    out = out.point(lambda p, u=umbral: 255 if p > u else 0)

                if invertir:
                    out = ImageOps.invert(out)

            if sharpen:
                out = ImageEnhance.Sharpness(out).enhance(1.0 + sharpen)

            return out

        except Exception as e:
            _log.warning("preprocesar_imagen falló (%s); fallback simple", e)
            out = img.convert("L")
            if escala and escala != 1:
                out = out.resize(
                    (int(out.width * escala), int(out.height * escala))
                )
            return ImageOps.autocontrast(out)


def variantes_reintento(crop, borde=12):
    """
    [M1] Generador PEREZOSO de variantes de preprocesamiento, en orden
    de probabilidad de éxito. Se consume solo hasta que un intento
    supera el umbral de confianza (salida temprana [M2]).

        0. Sin escalar (o escalado mínimo) — confirmado 06/07/2026: hay
           dígitos (ej. '8') que a escala 3x-8x el reescalado cúbico los
           deforma y Tesseract los confunde con otro dígito ('9'); a 1x-2x
           lee bien siempre. Se prueba primero por ser la zona más estable
           Y la más barata (sin resize).
        1. Pipeline estándar (denoise + CLAHE + adaptativo)
        2. Sin binarizar, sharpening fuerte
        3. Escala máxima + binarización dura
        4. Colores invertidos (texto claro / fondo oscuro)
    """
    base = ImageOps.expand(crop, border=borde, fill=255)
    esc = CONFIG.get("crop_escala", 3)
    esc_max = CONFIG.get("crop_escala_max", 5)

    yield preprocesar_imagen(base, escala=1, binarizar=False)
    yield preprocesar_imagen(base, escala=esc)
    yield preprocesar_imagen(base, escala=esc, binarizar=False, sharpen=1.5)
    yield preprocesar_imagen(base, escala=esc_max, binarizar=True, sharpen=0.8)
    yield preprocesar_imagen(base, escala=esc, invertir=True)


def preparar_pantalla(img):
    """
    Preprocesa la captura COMPLETA para el OCR de página.

    IGUAL al original (resize + autocontraste + contraste), SIN denoise
    ni CLAHE — ese pipeline [M1] deformaba dígitos finos a esta resolución
    (ej. '2' leído como '7' en Sociedad/fechas, marcador de radio button
    perdido en Tipo de Procesamiento). Confirmado en producción 06/07/2026.
    Los crops individuales sí usan preprocesar_imagen() con el pipeline nuevo.
    """
    escala = CONFIG.get("fullpage_escala", 2.0)
    out = img.convert("L")
    if escala and escala != 1:
        out = out.resize((int(out.width * escala), int(out.height * escala)))
    out = ImageOps.autocontrast(out)
    out = ImageEnhance.Contrast(out).enhance(CONFIG.get("contraste", 2.6))
    return out


# ============================================================
# [M1] MOTOR OCR MULTI-BACKEND + VALIDACIÓN DE CONFIANZA
# ============================================================

def _obtener_easyocr():
    """Singleton perezoso de EasyOCR (backend secundario opcional)."""
    global _EASYOCR_READER
    if _EASYOCR_READER is not None:
        return _EASYOCR_READER
    modo = CONFIG.get("backend_secundario", "auto")
    if modo == "off":
        return None
    try:
        import easyocr
        _EASYOCR_READER = easyocr.Reader(["es", "en"], gpu=False, verbose=False)
        _log.info("EasyOCR habilitado como backend secundario")
    except Exception:
        _EASYOCR_READER = None
        if modo == "easyocr":
            _log.warning("EasyOCR solicitado pero no disponible")
    return _EASYOCR_READER


def _ocr_easyocr(img):
    """OCR con EasyOCR. Devuelve (texto, confianza_media 0-100)."""
    reader = _obtener_easyocr()
    if reader is None:
        return None, -1.0
    try:
        arr = np.asarray(img.convert("RGB")) if _HAS_NUMPY else img
        res = reader.readtext(arr, detail=1, paragraph=False)
        if not res:
            return "", 0.0
        textos = [r[1] for r in res]
        confs = [float(r[2]) * 100 for r in res]
        return limpiar(" ".join(textos)), sum(confs) / len(confs)
    except Exception as e:
        _log.warning("EasyOCR falló: %s", e)
        return None, -1.0


def ocr_tesseract_conf(img, lang="eng", config="--psm 7"):
    """
    [M1] Tesseract con confianza: devuelve (texto, conf_media 0-100).
    conf_media = promedio de conf de palabras válidas; -1 si falló.
    """
    with cronometro("tesseract"):
        try:
            data = pytesseract.image_to_data(
                img, lang=lang, config=config,
                output_type=pytesseract.Output.DICT,
            )
        except Exception as e:
            _log.debug("image_to_data falló (%s), retry image_to_string", e)
            try:
                txt = pytesseract.image_to_string(img, lang=lang, config=config)
                return limpiar(txt), 0.0
            except Exception as e2:
                _log.warning("Tesseract falló por completo: %s", e2)
                return None, -1.0

    palabras, confs = [], []
    for i in range(len(data["text"])):
        t = limpiar(data["text"][i])
        if not t:
            continue
        try:
            c = float(data["conf"][i])
        except Exception:
            c = -1
        if c >= 0:
            palabras.append(t)
            confs.append(c)

    if not palabras:
        return "", 0.0
    return limpiar(" ".join(palabras)), sum(confs) / len(confs)


def ocr_con_confianza(crop, configs, lang="eng",
                      validador=None, conf_min=None):
    """
    [M1]+[M2] OCR robusto sobre un crop:

    - Recorre variantes de preprocesamiento (variantes_reintento) y
      configuraciones PSM de Tesseract.
    - SALIDA TEMPRANA: retorna en cuanto un resultado pasa el
      `validador` (regex/callable) con confianza >= conf_min.
    - Si Tesseract nunca alcanza el umbral, consulta el backend
      secundario (EasyOCR) si está habilitado.
    - Si nada pasa el validador, devuelve el mejor intento por
      confianza (comportamiento tolerante, como el original).

    Returns: (texto, confianza) — texto puede ser "" y conf -1.
    """
    if conf_min is None:
        conf_min = CONFIG.get("conf_min_campo", 55)

    def _valida(txt):
        if not txt:
            return False
        if validador is None:
            return True
        if callable(validador):
            return bool(validador(txt))
        return re.search(validador, txt) is not None

    mejor = ("", -1.0)
    max_var = 1 + CONFIG.get("max_reintentos_crop", 3)
    salida_temprana = CONFIG.get("salida_temprana", True)
    validos = []

    # NO confiar en una sola variante (ni la primera ni la de más confianza
    # reportada por Tesseract): confirmado 06/07/2026 que distintos campos
    # necesitan distintas escalas para leer bien el mismo tipo de dígito
    # (Importe lee mejor sin escalar, Cta.mayor lee mal sin escalar) y la
    # confianza de Tesseract no es fiable para decidir cuál confiar. Se
    # exige consenso: 2 lecturas independientes que coincidan.
    for n_var, variante in enumerate(variantes_reintento(crop)):
        if n_var >= max_var:
            break
        for cfg in configs:
            txt, conf = ocr_tesseract_conf(variante, lang=lang, config=cfg)
            if txt is None:
                continue
            if conf > mejor[1] and txt:
                mejor = (txt, conf)
            if _valida(txt):
                validos.append(txt)
                if salida_temprana and conf >= conf_min and validos.count(txt) >= 2:
                    return txt, conf

    if validos:
        conteo = {}
        for v in validos:
            conteo[v] = conteo.get(v, 0) + 1
        top_val, top_n = sorted(conteo.items(), key=lambda x: x[1], reverse=True)[0]
        return top_val, max(mejor[1], conf_min if top_n >= 2 else 0)

    # --- backend secundario como última instancia [M1] ---
    txt2, conf2 = _ocr_easyocr(crop)
    if txt2 and _valida(txt2) and conf2 >= conf_min:
        return txt2, conf2
    if txt2 and conf2 > mejor[1]:
        mejor = (txt2, conf2)

    return mejor


# ============================================================
# [M2] EJECUTOR PARALELO DE OCR + CACHE DE ROIs
# ============================================================

_EXECUTOR = None
_EXECUTOR_LOCK = threading.Lock()


def _executor():
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None:
            _EXECUTOR = ThreadPoolExecutor(
                max_workers=max(1, int(CONFIG.get("ocr_workers", 4))),
                thread_name_prefix="ocr",
            )
    return _EXECUTOR


def ocr_paralelo(tareas, detener_si=None):
    """
    [M2] Ejecuta funciones de OCR en paralelo.

    Args:
        tareas:     lista de callables sin argumentos.
        detener_si: callable(resultado) -> True corta el resto (cancela
                    futuros pendientes; los en vuelo terminan solos).

    Returns: lista de resultados no-None en orden de finalización.
    """
    resultados = []
    ex = _executor()
    futuros = [ex.submit(t) for t in tareas]
    try:
        for fut in as_completed(futuros):
            try:
                r = fut.result()
            except Exception as e:
                _log.debug("tarea OCR paralela falló: %s", e)
                continue
            if r is not None:
                resultados.append(r)
                if detener_si and detener_si(r):
                    for f in futuros:
                        f.cancel()
                    break
    finally:
        pass
    return resultados


class _CacheROI:
    """
    [M2] Cache de coordenadas: bboxes escalados por resolución y
    posiciones de labels ya localizados, para evitar recálculo.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._bbox = {}
        self._labels = {}

    def bbox_escalado(self, key, calcular):
        with self._lock:
            if key in self._bbox:
                return self._bbox[key]
        val = calcular()
        with self._lock:
            self._bbox[key] = val
        return val

    def label(self, key):
        with self._lock:
            return self._labels.get(key)

    def guardar_label(self, key, val):
        with self._lock:
            self._labels[key] = val

    def limpiar(self):
        with self._lock:
            self._bbox.clear()
            self._labels.clear()


_CACHE_ROI = _CacheROI()


# ============================================================
# OCR DE PÁGINA COMPLETA -> PALABRAS / LÍNEAS
# ============================================================

def obtener_ocr_data(img_proc):
    """Ejecuta Tesseract sobre la imagen (idéntico al original)."""
    with cronometro("tesseract_fullpage"):
        try:
            return pytesseract.image_to_data(
                img_proc, lang="spa", config="--psm 6",
                output_type=pytesseract.Output.DICT,
            )
        except Exception:
            return pytesseract.image_to_data(
                img_proc, lang="eng", config="--psm 6",
                output_type=pytesseract.Output.DICT,
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

        if conf < CONFIG.get("conf_min_palabra", 10):
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
                linea["cy"] = sum(x["cy"] for x in linea["palabras"]) / len(
                    linea["palabras"]
                )
                agregado = True
                break
        if not agregado:
            lineas.append({"cy": p["cy"], "palabras": [p]})

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


def obtener_palabras_lineas_desde_pantalla(forzar=False):
    """
    Captura pantalla, OCR, palabras y líneas.

    [M2] Captura diferencial: si la pantalla no cambió desde la última
    llamada (y el cache no expiró), devuelve el resultado cacheado sin
    volver a ejecutar Tesseract. `forzar=True` ignora el cache.
    """
    screenshot = capturar_ventana_sap()

    if CONFIG.get("captura_diferencial", True) and not forzar:
        cambio, firma = _CACHE_PANTALLA.hay_cambio(screenshot)
        if not cambio:
            cacheado = _CACHE_PANTALLA.obtener()
            if cacheado is not None:
                _log.info("[M2] Pantalla sin cambios — OCR cacheado reutilizado")
                METRICAS.registrar("ocr_cache_hit", 0.0)
                return cacheado
    else:
        firma = None

    escala = CONFIG.get("fullpage_escala", 2.0)
    img_proc = preparar_pantalla(screenshot)
    data = obtener_ocr_data(img_proc)
    palabras = extraer_palabras(data, escala=escala)
    lineas = agrupar_lineas(palabras)

    resultado = (screenshot, palabras, lineas)
    if firma is not None:
        _CACHE_PANTALLA.guardar(firma, resultado)
    return resultado


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


# ---------- utilidades numpy para análisis de píxeles [M2] ----------

def _mascara_oscuros(crop_gris, umbral):
    """
    Devuelve set de (x, y) con píxeles < umbral.
    Con numpy es ~100x más rápido que getpixel() punto a punto.
    """
    if _HAS_NUMPY:
        arr = np.asarray(crop_gris)
        ys, xs = np.where(arr < umbral)
        return set(zip(xs.tolist(), ys.tolist()))

    w, h = crop_gris.size
    px = crop_gris.load()
    return {(x, y) for y in range(h) for x in range(w) if px[x, y] < umbral}


# ============================================================
# ZFIEC015
# ============================================================

def extraer_sociedad_zfiec(palabras):
    """Extrae sociedad ZFIEC015 (lógica original)."""
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


def _recortar(img, bbox):
    """Recorta bbox saturado a los límites de la imagen, o None."""
    x1, y1, x2, y2 = bbox
    x1 = max(0, int(x1))
    y1 = max(0, int(y1))
    x2 = min(img.width, int(x2))
    y2 = min(img.height, int(y2))
    if x2 <= x1 or y2 <= y1:
        return None
    return img.crop((x1, y1, x2, y2)).convert("L")


def ocr_region_numero_largo(img, bbox):
    """
    OCR especializado para números largos (proveedor de 10 dígitos).

    [M1] Preprocesado con umbral adaptativo + reintentos por confianza.
    [M2] Salida temprana: el original ejecutaba 2 variantes x 3 configs
    = 6 llamadas siempre; ahora corta en la primera lectura válida
    de 10 dígitos con confianza suficiente.
    """
    crop = _recortar(img, bbox)
    if crop is None:
        return None

    configs = [
        "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789",
        "--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789",
        "--oem 3 --psm 13 -c tessedit_char_whitelist=0123456789",
    ]

    def _valida(txt):
        return re.fullmatch(r"\d{10}", re.sub(r"\D", "", txt)) is not None

    txt, conf = ocr_con_confianza(crop, configs, lang="eng",
                                  validador=_valida)
    solo = re.sub(r"\D", "", txt or "")
    if re.fullmatch(r"\d{10}", solo):
        _log.debug("numero_largo=%s conf=%.1f", solo, conf)
        return solo
    return None


def _componentes_conectados(mask, w, h, diag=True):
    """Flood-fill de componentes conectados sobre un set de puntos."""
    visitados = set()
    comps = []

    if diag:
        vecinos_rel = (
            (1, 0), (-1, 0), (0, 1), (0, -1),
            (1, 1), (-1, -1), (1, -1), (-1, 1),
        )
    else:
        vecinos_rel = ((1, 0), (-1, 0), (0, 1), (0, -1))

    for punto in list(mask):
        if punto in visitados:
            continue
        stack = [punto]
        visitados.add(punto)
        xs, ys = [], []

        while stack:
            xx, yy = stack.pop()
            xs.append(xx)
            ys.append(yy)
            for dx, dy in vecinos_rel:
                nx, ny = xx + dx, yy + dy
                if (
                    0 <= nx < w and 0 <= ny < h
                    and (nx, ny) in mask
                    and (nx, ny) not in visitados
                ):
                    visitados.add((nx, ny))
                    stack.append((nx, ny))

        comps.append((min(xs), min(ys), max(xs), max(ys), len(xs)))

    return comps


def detectar_codigo_01_por_pixeles(img, bbox):
    """
    Detecta código 01 por píxeles (lógica original).
    [M2] Máscara de oscuros construida con numpy (antes doble bucle
    getpixel por píxel).
    """
    crop = _recortar(img, bbox)
    if crop is None:
        return None

    w, h = crop.size
    mask = _mascara_oscuros(crop, 120)

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
        (xx, yy) for (xx, yy) in mask
        if yy not in filas_a_borrar and xx not in cols_a_borrar
    }

    comps = []
    for bx1, by1, bx2, by2, area in _componentes_conectados(mask, w, h):
        bw = bx2 - bx1 + 1
        bh = by2 - by1 + 1
        if area < 8:
            continue
        if bh < h * 0.18:
            continue
        if bw < 2:
            continue
        if bw > w * 0.80 or bh > h * 0.95:
            continue
        comps.append({"x1": bx1, "h": bh, "area": area})

    comps.sort(key=lambda c: c["x1"])
    comps_validos = [c for c in comps if c["h"] >= h * 0.20 and c["area"] >= 10]

    if len(comps_validos) >= 2:
        return "01"
    return None


def ocr_region_solo_digitos(img, bbox):
    """
    OCR de región de 1-2 dígitos (código tipo documento).

    [M2] Salida temprana en cuanto aparece "01" (criterio original:
    '01' tenía prioridad absoluta entre resultados) o cualquier lectura
    con confianza suficiente. El original ejecutaba SIEMPRE
    3 variantes x 4 configs = 12 llamadas a Tesseract.
    """
    crop = _recortar(img, bbox)
    if crop is None:
        return None

    configs = [
        "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789",
        "--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789",
        "--oem 3 --psm 13 -c tessedit_char_whitelist=0123456789",
        "--oem 3 --psm 10 -c tessedit_char_whitelist=0123456789",
    ]

    def _extrae(txt):
        solo = re.sub(r"\D", "", txt or "")
        if not solo:
            return None
        return solo.zfill(2) if len(solo) == 1 else solo[:2]

    # prioridad 1: detectar "01" (criterio original conservado)
    txt, conf = ocr_con_confianza(
        crop, configs, lang="eng",
        validador=lambda t: _extrae(t) == "01",
    )
    val = _extrae(txt)
    if val == "01":
        return "01"

    # prioridad 2: primer resultado numérico razonable
    if val and conf >= 0:
        return val

    # prioridad 3: fallback por análisis de píxeles (original)
    return detectar_codigo_01_por_pixeles(img, bbox)


def detectar_input_codigo_tipo_documento(img, linea_label):
    """
    Detecta bbox del input código tipo documento (lógica original).
    [M2] Cacheado por resolución + posición del label, y máscara de
    oscuros con numpy.
    """
    key = (
        "input_cod_tipo_doc",
        img.width, img.height,
        int(linea_label["left"]), int(linea_label["top"]),
    )

    def _calcular():
        alto = max(14, linea_label["bottom"] - linea_label["top"])

        sx1 = max(0, int(linea_label["right"] - alto * 0.8))
        sy1 = max(0, int(linea_label["top"] - alto * 1.4))
        sx2 = min(img.width, int(linea_label["right"] + alto * 8.5))
        sy2 = min(img.height, int(linea_label["bottom"] + alto * 2.2))

        search = img.crop((sx1, sy1, sx2, sy2)).convert("L")
        w, h = search.size
        dark = _mascara_oscuros(search, 90)

        comps = []
        for x1, y1, x2, y2, _area in _componentes_conectados(
            dark, w, h, diag=False
        ):
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

        ax1, ay1 = sx1 + bx1, sy1 + by1
        ax2, ay2 = sx1 + bx2, sy1 + by2

        margen_x = max(3, int((ax2 - ax1) * 0.10))
        margen_y = max(3, int((ay2 - ay1) * 0.18))

        return (
            max(0, ax1 + margen_x),
            max(0, ay1 + margen_y),
            min(img.width, ax2 - margen_x),
            min(img.height, ay2 - margen_y),
        )

    return _CACHE_ROI.bbox_escalado(key, _calcular)


def extraer_proveedor_zfiec(palabras, lineas, screenshot=None):
    """
    Extrae proveedor ZFIEC015 (estrategia original de 4 pasos).
    [M2] Los 3 crops de fallback se ejecutan en PARALELO con salida
    temprana (antes: secuenciales, cada uno con 6 llamadas Tesseract).
    """
    _log.info("ZFIEC015 OCR — buscando proveedor...")

    # 1. Línea completa con label Proveedor
    for idx, l in enumerate(lineas, start=1):
        texto_linea = l["texto"] if isinstance(l, dict) else str(l)
        if "proveedor" in normalizar(texto_linea):
            _log.info("ZFIEC015 OCR — línea proveedor [%s]: %r", idx, texto_linea)
            m = re.search(r"\b(\d{10})\b", texto_linea)
            if m:
                _log.info("ZFIEC015 OCR — proveedor en línea: %s", m.group(1))
                return m.group(1)

    # 2. Label Proveedor en palabras
    label = None
    for p in palabras:
        if "proveedor" in p["norm"]:
            label = p
            _log.info(
                "ZFIEC015 OCR — label proveedor: texto=%r left=%s right=%s "
                "top=%s bottom=%s cy=%s",
                p["texto"], p["left"], p["right"], p["top"], p["bottom"], p["cy"],
            )
            break

    if label:
        y_ref = label["cy"]
        candidatos = []
        for p in palabras:
            if abs(p["cy"] - y_ref) <= 45 and p["left"] > label["right"]:
                m = re.search(r"\b(\d{10})\b", p["texto"])
                if m:
                    candidatos.append((p["left"], m.group(1)))

        if candidatos:
            candidatos.sort(key=lambda x: x[0])
            proveedor = candidatos[-1][1]
            _log.info("ZFIEC015 OCR — proveedor por candidatos: %s", proveedor)
            return proveedor

        # 3. Fallback por crops a la derecha del label — EN PARALELO [M2]
        if screenshot is not None:
            alto = max(18, label["bottom"] - label["top"])
            bboxes = [
                (label["right"], label["top"] - alto * 1.2,
                 label["right"] + alto * 18, label["bottom"] + alto * 1.8),
                (label["right"], label["top"] - alto * 2.0,
                 label["right"] + alto * 24, label["bottom"] + alto * 2.5),
                (label["right"] - alto, label["top"] - alto * 1.5,
                 label["right"] + alto * 30, label["bottom"] + alto * 2.5),
            ]

            tareas = [
                (lambda b=b: ocr_region_numero_largo(screenshot, b))
                for b in bboxes
            ]
            encontrados = ocr_paralelo(tareas, detener_si=lambda r: bool(r))
            for proveedor in encontrados:
                if proveedor:
                    _log.info("ZFIEC015 OCR — proveedor por crop: %s", proveedor)
                    return proveedor
    else:
        _log.warning("ZFIEC015 OCR — no se encontró label proveedor")

    # 4. Búsqueda global de 10 dígitos (original)
    todos_textos = [
        l.get("texto", "") if isinstance(l, dict) else str(l) for l in lineas
    ]
    nums = re.findall(r"\b\d{10}\b", " ".join(todos_textos))
    if nums:
        _log.info("ZFIEC015 OCR — proveedor global 10 dígitos: %s", nums[-1])
        return nums[-1]

    _log.warning("ZFIEC015 OCR — proveedor no detectado")
    return None


def extraer_fechas_zfiec(lineas):
    """Extrae fechas ZFIEC015 (original)."""
    for l in lineas:
        texto = l["texto"] if isinstance(l, dict) else str(l)
        n = normalizar(texto)
        if "fecha" in n and "factura" not in n and "contab" not in n:
            fechas = re.findall(r"\d{2}[.,]\d{2}[.,]\d{4}", texto)
            if fechas:
                f_ini = fechas[0].replace(",", ".")
                f_fin = fechas[1].replace(",", ".") if len(fechas) >= 2 else None
                return f_ini, f_fin
    return None, None


def extraer_codigo_tipo_documento(palabras, lineas, screenshot):
    """Extrae código tipo documento ZFIEC015 (lógica original)."""
    linea_label = None
    for l in lineas:
        texto = limpiar(l["texto"])
        n = normalizar(texto)
        if (
            ("codigo" in n or "cedigo" in n or "cdigo" in n)
            and "tipo" in n and "documento" in n
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

    bbox_input = detectar_input_codigo_tipo_documento(screenshot, linea_label)
    valor = ocr_region_solo_digitos(screenshot, bbox_input)
    if valor:
        return valor

    x1, y1, x2, y2 = bbox_input
    pad_x = max(4, int((x2 - x1) * 0.25))
    pad_y = max(3, int((y2 - y1) * 0.35))
    return ocr_region_solo_digitos(
        screenshot, (x1 - pad_x, y1 - pad_y, x2 + pad_x, y2 + pad_y)
    )


def extraer_tipo_procesamiento_zfiec(lineas):
    """Detecta radio seleccionado (original)."""
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

    IMPORTANTE (conservado del original):
    - NO actualiza valores_bancos.json.
    - SOLO devuelve valores detectados.
    - valores_bancos.json solo debe ser actualizado por main.py.

    [M2] Un solo OCR de página compartido + proveedor deduplicado
    (el original llamaba extraer_proveedor_zfiec DOS veces: una en
    variable descartada y otra dentro del dict — ahora una sola vez).
    """
    METRICAS.reset()

    with cronometro("zfiec015_total"):
        screenshot, palabras, lineas = obtener_palabras_lineas_desde_pantalla()

        fecha_inicio, fecha_fin = extraer_fechas_zfiec(lineas)
        codigo_tipo_doc = extraer_codigo_tipo_documento(
            palabras, lineas, screenshot
        )
        proveedor = extraer_proveedor_zfiec(
            palabras, lineas, screenshot=screenshot
        )

        resultado = {
            "Sociedad": extraer_sociedad_zfiec(palabras),
            "Proveedor": proveedor,
            "FechaInicio": fecha_inicio,
            "FechaFin": fecha_fin,
            "Código Tipo de Documento": codigo_tipo_doc,
            "Tipo de Procesamiento": extraer_tipo_procesamiento_zfiec(lineas),
        }

    # SIEMPRE actualizar archivo de detectados OCR (original)
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

    _log.info(METRICAS.reporte())
    return resultado


def leer_y_validar_zfiec015():
    """
    Lee valores OCR de ZFIEC015 y compara contra valores_bancos.json.
    (Criterios de validación IDÉNTICOS al original.)
    """
    ruta_base = _BASE_DIR / "valores_bancos.json"

    try:
        if ruta_base.exists():
            with open(ruta_base, encoding="utf-8") as f:
                esperados = json.load(f)
        else:
            esperados = {}
    except Exception as e:
        _log.error("No se pudo leer valores_bancos.json: %s", e)
        esperados = {}

    detectados = leer_valores_zfiec015()
    diferencias = {}

    campos_json = [
        "Sociedad", "Proveedor", "FechaInicio", "FechaFin",
        "Código Tipo de Documento", "Tipo de Procesamiento",
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

def escalar_bbox_fb60(img, bbox):
    """Escala bbox calibrado 1580x1080. [M2] Cacheado por resolución."""
    key = ("fb60", img.width, img.height, bbox)

    def _calc():
        x1, y1, x2, y2 = bbox
        sx = img.width / CONFIG["fb60_base_w"]
        sy = img.height / CONFIG["fb60_base_h"]
        return (int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy))

    return _CACHE_ROI.bbox_escalado(key, _calc)


def ocr_crop_fb60(img, bbox, modo="texto", nombre_debug=None):
    """
    OCR sobre una región FB60.

    [M1] Preprocesado nuevo (denoise + adaptativo) con reintentos por
    confianza en lugar del pipeline fijo resize 4x + contraste.
    """
    crop = _recortar(img, escalar_bbox_fb60(img, bbox))
    if crop is None:
        return ""

    if nombre_debug:
        try:
            crop.save(str(_BASE_DIR / f"debug_{nombre_debug}.png"))
        except Exception:
            pass

    if modo == "fecha":
        configs = ["--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789./,"]
        lang = "eng"
        validador = r"\d{2}[.,]\d{2}[.,]\d{4}"
    elif modo == "decimal":
        configs = ["--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.,-Oo"]
        lang = "eng"
        validador = r"\d[.,]\d{2}"
    elif modo == "cuenta":
        configs = ["--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789"]
        lang = "eng"
        validador = r"\d{6,}"
    elif modo == "codigo":
        # Código corto de 1 letra dentro de un campo con borde (ej. Vía pago:
        # T, C, H...) — solo letras en el whitelist (sin dígitos) para que
        # Tesseract no confunda 'T' con '7'; psm 7 y 10 en paralelo porque
        # psm 8 (palabra única) solo no bastó para separar el glifo del borde.
        configs = [
            "--oem 3 --psm 10 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            "--oem 3 --psm 8 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        ]
        lang = "eng"
        validador = r"[A-Z]{1,3}"
    else:
        configs = ["--oem 3 --psm 7"]
        lang = "spa+eng"
        validador = None

    txt, _conf = ocr_con_confianza(crop, configs, lang=lang,
                                   validador=validador)
    return limpiar(txt or "")


def extraer_lineas_fb60(img):
    """
    Extrae líneas OCR FB60.
    [M2] Comparte el OCR de página con el cache diferencial cuando la
    imagen coincide con la última captura procesada.
    """
    escala = CONFIG.get("fullpage_escala", 2.0)
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
        # límite original (2100) estaba en coordenadas 2x — se conserva
        # el mismo recorte físico independientemente de la escala:
        if x > 2100 * (escala / 2.0):
            continue
        bloques.append((y, x, txt))

    bloques.sort(key=lambda b: (b[0], b[1]))

    lineas = []
    linea_actual = []
    y_ref = None
    tolerancia_y = 22 * (escala / 2.0)

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
    """Une líneas FB60 en un solo texto (original)."""
    txt = " ".join(lineas)
    for pat in ("|", "&lt;", "&gt;", "&amp;lt;", "&amp;gt;",
                "&amp;amp;lt;", "&amp;amp;gt;", "\u2014", "_"):
        txt = txt.replace(pat, " ")
    txt = txt.replace("*", " * ")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def normalizar_decimal(txt, permitir_1_decimal=False):
    """Normaliza importes SAP/OCR a formato 1234.56 (original).

    Args:
        permitir_1_decimal: si True, acepta también 1 solo dígito decimal
            (ej. '46.0' -> '46.00'). SAP a veces muestra un solo decimal
            (confirmado 14/07/2026). Debe activarse SOLO para configs con
            whitelist de dígitos (psm 7/8/13) — con psm 11 (sin whitelist)
            el cursor parpadeante se lee como dígito falso y se obtiene
            un decimal completo pero INVENTADO (ej. '46.08'), que un
            regex de 1-2 dígitos aceptaría igual sin poder distinguirlo
            del real. Con whitelist restringido a dígitos, ese falso
            positivo de 2 decimales completos no se produce.
    """
    if not txt:
        return None

    txt = limpiar(txt)
    txt = txt.replace("O", "0").replace("o", "0").replace(" ", "")

    n_dec = "1,2" if permitir_1_decimal else "2"
    m = re.search(
        r"-?\d{1,3}(?:[.,]\d{3})*[.,]\d{%s}|-?\d+[.,]\d{%s}" % (n_dec, n_dec), txt
    )
    if not m:
        return None

    num = m.group(0)
    if "." in num and "," in num:
        if num.rfind(".") > num.rfind(","):
            num = num.replace(",", "")
        else:
            num = num.replace(".", "").replace(",", ".")
    elif "," in num:
        num = num.replace(",", ".")

    try:
        return f"{float(num):.2f}"
    except Exception:
        return None


def importes_iguales(a, b):
    """Compara dos importes a 2 decimales (original)."""
    try:
        na = normalizar_decimal(str(a))
        nb = normalizar_decimal(str(b))
        if na is None or nb is None:
            return False
        return round(float(na), 2) == round(float(nb), 2)
    except Exception:
        return False


def ocr_decimal_preciso_fb60(img, bbox, nombre_debug=None,
                             valor_referencia=None):
    """
    OCR especializado para importes.

    [M2] El original ejecutaba SIEMPRE 4 variantes x 3 configs = 12
    llamadas a Tesseract por bbox. Ahora: variantes perezosas, cortando
    en cuanto 2 lecturas coinciden entre sí (consenso, no confianza
    aislada — ver TODO más abajo, 22/07/2026).
    Devuelve lista de valores normalizados (interfaz original).
    """
    crop = _recortar(img, escalar_bbox_fb60(img, bbox))
    if crop is None:
        return []

    if nombre_debug:
        try:
            crop.save(str(_BASE_DIR / f"debug_{nombre_debug}.png"))
        except Exception:
            pass

    configs = [
        "--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789.,Oo",
        "--oem 3 --psm 13 -c tessedit_char_whitelist=0123456789.,Oo",
        # psm 11 (texto disperso) sin whitelist: psm 7 trunca antes de los
        # centavos cuando hay un cursor de texto "|" parpadeando justo
        # después del número (confirmado 06/07/2026, ej. '44455.09' leído
        # como '44455.'). normalizar_decimal() limpia el ruido sobrante.
        "--oem 3 --psm 11",
        # psm 7 al final: confirmado 14/07/2026 que agrega un "2" espurio
        # en recortes con label a la izquierda del campo (ej. '0.52' leído
        # como '20.52' o '220.52') — psm 8/13/11 leen el mismo recorte bien.
        "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.,Oo",
    ]

    resultados = []
    max_var = 1 + CONFIG.get("max_reintentos_crop", 3)

    for n_var, variante in enumerate(variantes_reintento(crop)):
        if n_var >= max_var:
            break
        for cfg in configs:
            txt, _conf = ocr_tesseract_conf(variante, lang="eng", config=cfg)
            valor = normalizar_decimal(txt) if txt else None
            if valor:
                resultados.append(valor)
                # TODO: fix — se quitó el retorno inmediato de una sola lectura
                # confiada (conf>=conf_min o n_var==0): confirmado 22/07/2026
                # que la variante 0 (sin escalar) puede leer mal con confianza
                # ALTA (ej. '20.96' con conf=64 en vez de '20.90' real, que las
                # variantes escaladas 1-4 leían bien con conf 87-95). Ahora se
                # exige el consenso de 2 lecturas iguales (chequeo de abajo)
                # antes de confiar en cualquier resultado — mismo criterio que
                # ya usa ocr_con_confianza() para el resto de los campos.
        # tras la primera variante, si ya hay >=2 lecturas idénticas, cortar
        if CONFIG.get("salida_temprana", True) and len(resultados) >= 2:
            if resultados.count(resultados[-1]) >= 2:
                return resultados

    if resultados:
        return resultados

    # Fallback: SAP a veces muestra solo 1 decimal (ej. '46.0' en vez de
    # '46.00' — confirmado 14/07/2026, cursor parpadeante tapando el 2º
    # dígito). Se activa SOLO si la extracción estricta (2 decimales) no
    # produjo NINGÚN candidato en ningún bbox/variante/config — para no
    # contaminar el voto por mayoría normal con lecturas de menor precisión.
    # Exige que ≥2 configs coincidan entre sí sobre variante 0 antes de
    # aceptar un valor — un solo testigo no basta (evita aceptar una
    # lectura corta y equivocada, ej. '0.52' mal leído como '0.0').
    crop0 = next(variantes_reintento(crop))
    relajados = []
    for cfg in configs:
        txt, _conf = ocr_tesseract_conf(crop0, lang="eng", config=cfg)
        v = normalizar_decimal(txt, permitir_1_decimal=True) if txt else None
        if v:
            relajados.append(v)
    conteo_relajado = {}
    for v in relajados:
        conteo_relajado[v] = conteo_relajado.get(v, 0) + 1
    if conteo_relajado:
        top_val, top_n = sorted(conteo_relajado.items(), key=lambda x: x[1], reverse=True)[0]
        if top_n >= 2:
            _log.info("[DEBUG] Importe fallback 1-decimal aceptado: %s (%s)", top_val, conteo_relajado)
            return [top_val]

    return resultados


def fecha_valida_fb60(fecha):
    """Valida fecha DD.MM.YYYY (original)."""
    if not fecha:
        return False
    try:
        datetime.datetime.strptime(fecha, "%d.%m.%Y")
        return True
    except Exception:
        return False


def extraer_titulo_fb60_v2(lineas):
    """Extrae título FB60 (original)."""
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
        txt, re.IGNORECASE,
    )
    if m:
        return limpiar(m.group(1))
    return None


def _primer_resultado_paralelo(img, bboxes, modo, patron):
    """[M2] OCR de bboxes alternativos en paralelo, corta al primero válido."""
    def _tarea(bbox):
        txt = ocr_crop_fb60(img, bbox, modo=modo)
        m = re.search(patron, txt)
        return m.group(0) if m else None

    tareas = [(lambda b=b: _tarea(b)) for b in bboxes]
    res = ocr_paralelo(tareas, detener_si=lambda r: bool(r))
    for r in res:
        if r:
            return r
    return None


def extraer_fechas_fb60_v2(img, lineas):
    """
    Extrae fechas FB60.
    [M2] Fecha factura y fecha contab. se procesan EN PARALELO y cada
    grupo de bboxes alternativos corta en el primer acierto.
    """
    bboxes_factura = [
        (215, 340, 390, 385), (220, 345, 355, 380), (218, 342, 370, 382),
    ]
    bboxes_contab = [
        (215, 385, 390, 425), (220, 390, 355, 420), (218, 387, 370, 422),
    ]

    patron = r"\d{2}[.,]\d{2}[.,]\d{4}"

    def _leer(bboxes):
        raw = _primer_resultado_paralelo(img, bboxes, "fecha", patron)
        if raw:
            f = raw.replace(",", ".")
            if fecha_valida_fb60(f):
                return f
        return None

    ex = _executor()
    fut_f = ex.submit(_leer, bboxes_factura)
    fut_c = ex.submit(_leer, bboxes_contab)
    fecha_factura = fut_f.result()
    fecha_contab = fut_c.result()

    # Fallback sobre el texto de página (original)
    if not fecha_factura or not fecha_contab:
        txt = texto_fb60_unido(lineas)
        fechas = [f.replace(",", ".") for f in re.findall(patron, txt)]
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
        (215, 425, 445, 465), (210, 420, 455, 470), (220, 430, 440, 460),
    ]

    for bbox in bboxes:
        txt = ocr_crop_fb60(img, bbox, modo="texto")
        n = normalizar(txt)
        if "facturaacreedor" in n or ("factura" in n and "acreedor" in n):
            return "Factura acreedor"
        if txt and len(txt) > 2:
            # Valor real (ej. "Tiquetes Aéreos") p/ que la validación compare
            return limpiar(txt)

    for l in lineas:
        n = normalizar(l)
        if "facturaacreedor" in n:
            return "Factura acreedor"
        if "clasedoc" in n and "factura" in n:
            return "Factura acreedor"

    # El título SIEMPRE dice "Registrar factura de acreedor" — no se usa
    # como fallback para evitar falsos positivos (comentario original).
    return None


def extraer_importe_fb60_v2(img, lineas):
    """
    Extrae importe cabecera FB60.
    [M2] Los 4 bboxes se OCRean en paralelo; se corta cuando dos
    lecturas coinciden (equivalente al voto por mayoría original).
    """
    bboxes = [
        (205, 498, 315, 546), (208, 500, 300, 542),
        (200, 496, 360, 548), (200, 496, 700, 548),
    ]

    candidatos = []
    lock = threading.Lock()

    def _tarea(bbox, idx):
        # nombre_debug=f"importe_crop_{idx}" — descomentar para recalibrar bboxes
        vals = ocr_decimal_preciso_fb60(img, bbox)
        with lock:
            candidatos.extend(vals)
        return vals or None

    def _mayoria_alcanzada(_r):
        with lock:
            if not candidatos:
                return False
            conteo = {}
            for c in candidatos:
                conteo[c] = conteo.get(c, 0) + 1
            return max(conteo.values()) >= 2

    tareas = [
        (lambda b=b, i=i: _tarea(b, i))
        for i, b in enumerate(bboxes, start=1)
    ]
    ocr_paralelo(tareas, detener_si=_mayoria_alcanzada)

    if candidatos:
        conteo = {}
        for c in candidatos:
            conteo[c] = conteo.get(c, 0) + 1
        top_val, top_n = sorted(conteo.items(), key=lambda x: x[1], reverse=True)[0]
        # Sin mayoría real entre candidatos que no coinciden — no adivinar
        # (mismo criterio aplicado a Importe moneda doc., 06/07/2026).
        if not (top_n < 2 and len(conteo) > 1):
            return top_val

    txt = texto_fb60_unido(lineas)
    m = re.search(r"Importe\s*[:\s]*([0-9]+[.,][0-9]{2})", txt, re.IGNORECASE)
    if m:
        return normalizar_decimal(m.group(1))
    return None


def extraer_calc_impuestos_fb60_v2(img, lineas):
    """
    Detecta estado del checkbox Calc.Impuestos por análisis de píxeles.
    [M2] Conteo de píxeles azules vectorizado con numpy si disponible.
    """
    txt = texto_fb60_unido(lineas).lower()
    if "calc.impuestos" not in txt and "calc impuestos" not in txt:
        return None  # pantalla incorrecta

    x1, y1, x2, y2 = escalar_bbox_fb60(img, (450, 542, 490, 578))
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(img.width, x2), min(img.height, y2)
    if x2 <= x1 or y2 <= y1:
        return None

    crop_img = img.crop((x1, y1, x2, y2)).convert("RGB")

    # El contorno del checkbox SAP es azul incluso vacío — hay que ubicar
    # ese borde por los propios píxeles azules y analizar SOLO el interior
    # estricto (más adentro que el borde), donde va la tilde si está marcado.
    #
    # Se probó ampliar la detección a gris/negro además de azul (14/07/2026,
    # tras ver un checkbox marcado sin ningún píxel azul) pero se revirtió
    # el mismo día: en producción dio falso positivo con ratio alto
    # (0.18) en un checkbox confirmado VACÍO — el sombreado/borde del
    # control vacío también es oscuro y contamina la detección. Un falso
    # positivo aquí (dice marcado sin estarlo) es más peligroso que un
    # falso negativo (bloquea el doc para revisión manual), así que se
    # mantiene SOLO azul aunque implique fallar en el caso gris raro.
    if _HAS_NUMPY:
        arr_full = np.asarray(crop_img, dtype=np.int16)
        mask_full = (arr_full[..., 2] > arr_full[..., 0] + 40) & (arr_full[..., 2] > 100)
        ys, xs = np.where(mask_full)
        if ys.size == 0:
            azules, total = 0, 1
            interior_img = crop_img
        else:
            y0, y1b, x0, x1b = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
            # Margen mínimo subido de 2px a 6px (14/07/2026): confirmado en
            # producción que un checkbox VACÍO dio falso positivo (60/256
            # azules, 23%) — el margen de 20%/2px no alcanzaba a excluir el
            # borde (posible anillo de foco azul, más grueso que el borde
            # simple). Un margen insuficiente aquí es peligroso: prioriza
            # excluir de más antes que contar borde como tilde.
            iy = max(6, int((y1b - y0) * 0.3))
            ix = max(6, int((x1b - x0) * 0.3))
            yi0, yi1 = y0 + iy, y1b - iy
            xi0, xi1 = x0 + ix, x1b - ix
            if yi1 <= yi0 or xi1 <= xi0:
                # Bbox de azules demasiado chico para el margen 6px/30% sin
                # vaciarse por completo — confirmado 17/07/2026: un checkmark
                # real de ~10x11px quedaba en 0 tras el margen, aunque el
                # tilde SÍ estaba presente (mapa de píxeles confirmó forma de
                # check). En vez de asumir "vacío", se cuenta directo sobre
                # el bbox de azules ya detectado (sin trimming) — un
                # checkmark real llena buena parte de su propio bbox
                # (ratio 0.35 visto en producción; umbral sigue en 0.08).
                interior_mask = mask_full[y0:y1b + 1, x0:x1b + 1]
                azules = int(interior_mask.sum())
                total = int(interior_mask.size)
                interior_img = crop_img.crop((x0, y0, x1b + 1, y1b + 1))
            else:
                interior_mask = mask_full[yi0:yi1, xi0:xi1]
                azules = int(interior_mask.sum())
                total = int(interior_mask.size)
                interior_img = crop_img.crop((xi0, yi0, xi1, yi1))
    else:
        # Sin numpy: fallback conservador — recorta 30% de margen fijo.
        w, h = crop_img.size
        mx, my = max(2, int(w * 0.3)), max(2, int(h * 0.3))
        interior_img = crop_img.crop((mx, my, max(mx + 1, w - mx), max(my + 1, h - my)))
        pixels = list(interior_img.getdata())
        if not pixels:
            return None
        azules = sum(1 for rr, gg, bb in pixels if bb > rr + 40 and bb > 100)
        total = len(pixels)

    if not total:
        return None

    # crop_img.save(...) / interior_img.save(...) — descomentar para recalibrar margen/umbral
    _log.info("[DEBUG] Calc.impuestos interior=%s azules=%d/%d (%.4f)",
              interior_img.size, azules, total, azules / total)

    # Tilde SAP es azul — B >> R indica checkbox marcado (criterio original).
    # Umbral subido de 0.01 a 0.08 (14/07/2026) — capa extra de seguridad
    # junto con el margen más grande: los casos CONFIRMADOS marcados en
    # producción dieron ratios 0.18-0.24, muy por encima; un checkbox vacío
    # con fuga de borde debería quedar ahora por debajo de 0.08.
    return (azules / total) > 0.08


def extraer_combo_b2_fb60_v2(img, lineas):
    """Extrae el indicador de impuesto del combo FB60 (B1, B2, B3, …)."""
    txt = ocr_crop_fb60(img, (455, 585, 745, 625), modo="texto")

    # Solo leer del crop del combo — sin fallback a líneas completas para
    # evitar falsos positivos desde la columna indicador (ej. "0.00 B1").
    m = re.search(r"\b([Bb8][0-9])\b", txt)
    if m:
        codigo = m.group(1).upper().replace("8", "B")
        if re.search(r"iva|compras|15", txt, re.IGNORECASE):
            return f"{codigo} (IVA Compras 15% Cred...)"
        return codigo
    return None


def extraer_importe_moneda_doc_fb60(img, lineas, importe_referencia=None):
    """
    Extrae Importe moneda doc. — lectura INDEPENDIENTE de la cabecera.

    `importe_referencia` ya NO se usa como criterio de aceptación: usarlo
    para aceptar "el primer candidato que coincida con la cabecera" hacía
    que, entre ~120 intentos (10 bboxes x 4 variantes x 3 configs), casi
    siempre apareciera una lectura ruidosa que coincidía por azar con la
    cabecera — confirmando falsamente que tabla == cabecera aunque en
    pantalla dijeran valores distintos. Bug confirmado en pruebas 06/07/2026
    (cabecera=4.09, tabla real=4.08, siempre devolvía 4.09).

    Ahora se hace voto por mayoría entre las propias lecturas de la tabla
    (igual criterio que extraer_importe_fb60_v2 para la cabecera): corta
    en cuanto dos lecturas independientes coinciden ENTRE SÍ, nunca contra
    la cabecera.
    """
    bboxes_importe_tabla = [
        (600, 780, 930, 885), (620, 790, 930, 875), (650, 800, 930, 870),
        (680, 805, 920, 865), (700, 810, 910, 860), (460, 780, 980, 890),
        (580, 800, 1000, 875), (720, 790, 980, 875), (690, 810, 880, 870),
        (700, 815, 900, 870),
    ]

    candidatos = []
    candidatos_debug = []
    lock = threading.Lock()

    def _tarea(bbox, idx):
        # NO se agrega ocr_crop_fb60(modo="decimal") como intento extra:
        # usa ocr_con_confianza (sin el ajuste de confianza de la variante 0)
        # y podía sumar un voto erróneo que contaminaba el voto por mayoría
        # (bug 06/07/2026). ocr_decimal_preciso_fb60 ya es suficiente.
        # nombre_debug=f"importe_moneda_doc_{idx}" — descomentar para recalibrar bboxes
        vals = ocr_decimal_preciso_fb60(img, bbox)
        with lock:
            candidatos.extend(vals)
            candidatos_debug.append((idx, bbox, list(vals)))
        return vals or None

    def _mayoria_alcanzada(_r):
        with lock:
            if not candidatos:
                return False
            conteo = {}
            for c in candidatos:
                conteo[c] = conteo.get(c, 0) + 1
            return max(conteo.values()) >= 2

    tareas = [
        (lambda b=b, i=i: _tarea(b, i))
        for i, b in enumerate(bboxes_importe_tabla, start=1)
    ]
    ocr_paralelo(tareas, detener_si=_mayoria_alcanzada)

    _log.info("[DEBUG] Importe moneda doc. candidatos por bbox: %s", candidatos_debug)

    candidatos_limpios = [
        v for v in (normalizar_decimal(c) for c in candidatos) if v
    ]
    if not candidatos_limpios:
        return None

    conteo = {}
    for c in candidatos_limpios:
        conteo[c] = conteo.get(c, 0) + 1
    _log.info("[DEBUG] Importe moneda doc. conteo final: %s", conteo)

    top_val, top_n = sorted(conteo.items(), key=lambda x: x[1], reverse=True)[0]
    if top_n < 2 and len(conteo) > 1:
        # Sin mayoría real (todos los candidatos distintos, empate en 1) —
        # no adivinar entre lecturas que no coinciden entre sí, devolver
        # None. Confirmado 06/07/2026: con montos grandes, varios bboxes
        # devuelven valores distintos (uno truncado tipo '0.00', otro casi
        # correcto) y elegir cualquiera arbitrariamente puede dar el malo.
        _log.warning(
            "Importe moneda doc.: candidatos sin consenso %s — se reporta None",
            conteo,
        )
        return None
    return top_val


def extraer_tabla_fb60_v2(img, lineas, importe_referencia=None):
    """Extrae tabla FB60 sin valores quemados (lógica original)."""
    resultado = {
        "Cta.mayor": None,
        "Importe moneda doc.": None,
        "Texto": None,
        "Centro coste": None,
    }

    txt = texto_fb60_unido(lineas)

    cuentas = re.findall(r"\b\d{9,12}\b", txt)
    cuentas_validas = [c for c in cuentas if not re.fullmatch(r"20\d{8}", c)]

    # Cuentas GL de ASIAUTO empiezan con 8 — ignorar proveedores (1000xxxxxx)
    preferidas = [c for c in cuentas_validas if c.startswith("8")]
    if preferidas:
        resultado["Cta.mayor"] = preferidas[0]

    if not resultado["Cta.mayor"]:
        # nombre_debug="cta_mayor" — descomentar para recalibrar bbox
        cta_txt = ocr_crop_fb60(img, (85, 820, 220, 860), modo="cuenta")
        _log.info("[DEBUG] Cta.mayor crop OCR -> %r", cta_txt)
        m = re.search(r"\b8\d{8,11}\b", cta_txt)
        if m:
            resultado["Cta.mayor"] = m.group(0)

    # NO usar texto de página completa para el importe de tabla: incluye la
    # cabecera, así que "candidato igual a la referencia" siempre matchea
    # contra la propia cabecera sin verificar la tabla (bug 06/07/2026).
    resultado["Importe moneda doc."] = extraer_importe_moneda_doc_fb60(
        img, lineas, importe_referencia=importe_referencia
    )

    if "COMISION" in txt.upper():
        m = re.search(r"(COMISION\s+[A-ZÁÉÍÓÚÑ0-9. ]{0,40})", txt, re.IGNORECASE)
        resultado["Texto"] = limpiar(m.group(1)) if m else "COMISION"

    if not resultado["Texto"]:
        # nombre_debug="texto_tabla" — descomentar para recalibrar bbox
        texto_txt = ocr_crop_fb60(img, (930, 820, 1070, 860), modo="texto")
        _log.info("[DEBUG] Texto crop OCR -> %r", texto_txt)
        if texto_txt:
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
    """Valida solo que aparezca 'COMISION' (mayúsculas).

    El resto del texto (BANCO + nombre del banco) se omite — a diferencia
    de Txt.cabec. en pestaña Detalle, esta columna de la tabla de
    posiciones SAP la muestra SIEMPRE truncada en pantalla a 'COMISION
    BA...' (columna angosta) y nunca llega a mostrar 'BANCO' completo, así
    que exigirlo aquí haría fallar todos los documentos, no solo casos
    puntuales (confirmado 22/07/2026 revisando el historial de logs de la
    sesión — el truncamiento es constante en todos los bancos).
    """
    det_raw = limpiar(str(val_det or "")).upper()
    if not det_raw:
        return False
    return "COMISION" in det_raw


def leer_valores_fb60():
    """
    Lee todos los campos FB60.

    [M2] Los extractores independientes (título, clase doc, checkbox,
    combo) corren en paralelo con las fechas; los que dependen del
    importe de cabecera (tabla) se encadenan después.
    """
    METRICAS.reset()

    with cronometro("fb60_total"):
        screenshot = capturar_ventana_sap()
        lineas = extraer_lineas_fb60(screenshot)

        ex = _executor()

        fut_fechas = ex.submit(extraer_fechas_fb60_v2, screenshot, lineas)
        fut_titulo = ex.submit(extraer_titulo_fb60_v2, lineas)
        fut_clase = ex.submit(extraer_clase_documento_fb60_v2, screenshot, lineas)
        fut_check = ex.submit(extraer_calc_impuestos_fb60_v2, screenshot, lineas)
        fut_combo = ex.submit(extraer_combo_b2_fb60_v2, screenshot, lineas)

        importe_cabecera = extraer_importe_fb60_v2(screenshot, lineas)

        tabla = extraer_tabla_fb60_v2(
            screenshot, lineas, importe_referencia=importe_cabecera
        )

        fecha_factura, fecha_contab = fut_fechas.result()

        resultado = {
            "Titulo": fut_titulo.result(),
            "Clase documento": fut_clase.result(),
            "Fecha factura": fecha_factura,
            "Fecha contab.": fecha_contab,
            "Calc.impuestos": fut_check.result(),
            "Combo B2": fut_combo.result(),
            "Importe": importe_cabecera,
            "Cta.mayor": tabla.get("Cta.mayor"),
            "Importe moneda doc.": tabla.get("Importe moneda doc."),
            "Texto": tabla.get("Texto"),
            "Centro coste": tabla.get("Centro coste"),
        }

        # Quitada la regla que heredaba el importe de cabecera cuando la
        # tabla no se detectaba — fabricaba un valor no leído realmente
        # (confirmado 06/07/2026: al borrar el campo en SAP, igual
        # reportaba el valor de cabecera en vez de None). Ahora "Importe
        # moneda doc." siempre refleja lo que el OCR detectó de verdad.

    _log.info(METRICAS.reporte())
    return resultado


def validar_campos_fb60(valores):
    """Valida presencia, formato y coherencia FB60 (IDÉNTICO al original)."""
    errores = {}

    obligatorios = [
        "Titulo", "Clase documento", "Fecha factura", "Fecha contab.",
        "Calc.impuestos", "Combo B2", "Importe", "Cta.mayor",
        "Importe moneda doc.", "Texto", "Centro coste",
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
    Compara valores OCR detectados en FB60 contra esperados en
    valores_fb60.json. (Criterios de validación IDÉNTICOS al original.)
    """
    ruta_base = _BASE_DIR / "valores_fb60.json"

    try:
        if ruta_base.exists():
            with open(ruta_base, encoding="utf-8") as f:
                esperados = json.load(f)
        else:
            esperados = {}
    except Exception as e:
        _log.error("No se pudo leer valores_fb60.json: %s", e)
        esperados = {}

    detectados = leer_valores_fb60()
    diferencias = {}

    campos_json = [
        "Titulo", "Clase documento", "Calc.impuestos", "Combo B2",
        "Cta.mayor", "Texto", "Centro coste",
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
            # SAP agrega descripción larga al código ("B2 (IVA Compras...)")
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
        diferencias["Importe"] = (
            f"cabecera no detectada: cabecera={imp_cab!r} tabla={imp_tab!r}"
        )
    elif imp_tab is None or str(imp_tab).strip() == "":
        diferencias["Importe moneda doc."] = (
            f"tabla no detectada: cabecera={imp_cab!r} tabla={imp_tab!r}"
        )
    else:
        try:
            imp_cab_num = round(float(str(imp_cab).replace(",", ".")), 2)
            imp_tab_num = round(float(str(imp_tab).replace(",", ".")), 2)
            if imp_cab_num != imp_tab_num:
                diferencias["Importe"] = f"cabecera={imp_cab!r} tabla={imp_tab!r}"
        except Exception:
            diferencias["Importe"] = (
                f"no numérico: cabecera={imp_cab!r} tabla={imp_tab!r}"
            )

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
    """Extrae Txt.cabec. de pestaña Detalle (original + crops paralelos)."""
    for l in lineas:
        if "cabec" in normalizar(l):
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

    def _tarea(bbox):
        txt = ocr_crop_fb60(img, bbox, modo="texto")
        return limpiar(txt) if txt and len(txt) > 2 else None

    res = ocr_paralelo(
        [(lambda b=b: _tarea(b)) for b in bboxes],
        detener_si=lambda r: bool(r),
    )
    for r in res:
        if r:
            return r
    return None


def leer_valores_fb60_detalle():
    """Lee FB60 Detalle."""
    METRICAS.reset()
    with cronometro("fb60_detalle_total"):
        screenshot = capturar_ventana_sap()
        lineas = extraer_lineas_fb60(screenshot)
        resultado = {
            "Txt.cabec.": extraer_txt_cabecera_detalle_fb60(screenshot, lineas),
        }
    _log.info(METRICAS.reporte())
    return resultado


def leer_y_validar_fb60_detalle():
    """Compara Txt.cabec. contra Texto Cabecera en valores_bancos.json."""
    ruta = _BASE_DIR / "valores_bancos.json"

    try:
        if ruta.exists():
            with open(ruta, encoding="utf-8") as f:
                esperados = json.load(f)
        else:
            esperados = {}
    except Exception as e:
        _log.error("No se pudo leer valores_bancos.json: %s", e)
        esperados = {}

    detectados = leer_valores_fb60_detalle()
    diferencias = {}

    val_esp = esperados.get("Texto Cabecera")
    if val_esp is not None:
        val_det = detectados.get("Txt.cabec.")
        n_det = normalizar(str(val_det or ""))
        # Solo valida hasta "BANCO" — el nombre específico del banco que
        # sigue (DINERS, AUSTRO, etc.) se omite: el OCR lo lee bien de forma
        # inconsistente entre bancos y esa parte no es crítica para el
        # registro (pedido explícito 22/07/2026).
        if "banco" not in n_det:
            diferencias["Txt.cabec."] = (
                f"esperado={val_esp!r} detectado={_nd(val_det)}"
            )

    if diferencias:
        _log.error("Validación FB60 Detalle fallida:")
        for k, v in detectados.items():
            _log.error("  %s: %r", k, v)
    else:
        _log.info(
            "Validación OCR FB60 Detalle OK — Txt.cabec.: %r",
            detectados.get("Txt.cabec."),
        )

    return {
        "detectados": detectados,
        "diferencias": diferencias,
        "valido": len(diferencias) == 0,
    }


# ============================================================
# FB60 PAGO
# ============================================================

def extraer_via_pago_fb60(img, lineas=None):
    """Extrae Vía pago de la pestaña Pago (crop del interior del campo).

    Los bboxes cubren SOLO el interior de la caja del campo (perfil de
    píxeles medido 07/07/2026: bordes en x=159/181, icono ayuda F4 en
    x=184-206, líneas grises horizontales en y=458/466, glifo en y=475-487).
    Incluir el icono hacía que Tesseract lo leyera como 'T' con el campo
    vacío; incluir las líneas superiores hacía leer 'T' como 'F'.
    Guarda de píxeles: si el interior casi no tiene tinta el campo está
    vacío → None, sin llamar a Tesseract.
    Fallback de línea OCR: acepta solo UN carácter suelto tras la
    etiqueta (la vía de pago SAP es de 1 letra) para no capturar
    fragmentos de "Bloq.pago", que comparte la fila en pantalla.
    `lineas` es perezoso: si no se pasa, el OCR de página completa
    (~2-2.5s) se ejecuta SOLO si los 3 crops fallan.
    """
    bboxes = [
        (160, 469, 181, 492),
        (159, 468, 182, 493),
        (161, 470, 180, 491),
    ]
    _MIN_PIXELES_TINTA = 8   # menos que esto en el interior = campo vacío

    def _valor_sin_etiqueta(txt):
        """Descarta letras de la propia etiqueta ('Vía'/'Pago') coladas en el crop."""
        limpio = quitar_acentos(txt).lower()
        limpio = re.sub(r"\bvia\b|\bpago\b", " ", limpio)
        m = re.search(r"[A-Za-z]", limpio)
        return m.group(0).upper() if m else None

    def _tarea(bbox, idx):
        crop = _recortar(img, escalar_bbox_fb60(img, bbox))
        if crop is None:
            return None
        gris = crop.convert("L")
        px = gris.load()
        tinta = sum(
            1 for y in range(gris.height) for x in range(gris.width)
            if px[x, y] < 128
        )
        if tinta < _MIN_PIXELES_TINTA:
            _log.info("Vía pago bbox %d: %d px de tinta — campo vacío", idx, tinta)
            return None
        # nombre_debug=f"via_pago_{idx}" — descomentar para recalibrar bboxes
        txt = ocr_crop_fb60(img, bbox, modo="codigo")
        return _valor_sin_etiqueta(txt) if txt else None

    res = ocr_paralelo(
        [(lambda b=b, i=i: _tarea(b, i)) for i, b in enumerate(bboxes, start=1)],
        detener_si=lambda r: bool(r),
    )
    for r in res:
        if r:
            return r

    if lineas is None:
        lineas = extraer_lineas_fb60(img)
    for l in lineas:
        if re.search(r"v[ií]a\s*pago", normalizar(l)):
            # Solo UN carácter aislado tras la etiqueta — un token más largo
            # es "Bloq.pago"/"Autorizado..." de la misma fila, no el valor.
            m = re.search(r"v[ií]a\s*pago\s*[:\s]+([A-Za-z])(?![A-Za-z0-9.])", l, re.IGNORECASE)
            if m:
                val = _valor_sin_etiqueta(m.group(1))
                if val:
                    return val
    return None


def leer_valores_fb60_pago():
    """Lee FB60 Pago."""
    METRICAS.reset()
    with cronometro("fb60_pago_total"):
        screenshot = capturar_ventana_sap()
        # sin extraer_lineas_fb60 aquí — extraer_via_pago_fb60 lo ejecuta
        # perezosamente solo si los 3 crops fallan (ahorra ~2-2.5s/doc)
        resultado = {
            "Vía pago": extraer_via_pago_fb60(screenshot),
        }
    _log.info(METRICAS.reporte())
    return resultado


def leer_y_validar_fb60_pago():
    """Compara Vía pago contra VIA_PAGO (.env).

    NOTA: SAP GUI Scripting NO es opción para leer este campo — el servidor
    PS4 PRODUCCION lo tiene deshabilitado (DisabledByServer=True, verificado
    07/07/2026): la conexión expone 0 sesiones. Solo OCR.
    """
    detectados = leer_valores_fb60_pago()
    diferencias = {}

    val_esp = os.getenv("VIA_PAGO", "")
    if val_esp:
        val_det = detectados.get("Vía pago")
        n_esp = normalizar(val_esp)
        n_det = normalizar(str(val_det or ""))
        if n_esp != n_det:
            diferencias["Vía pago"] = f"esperado={val_esp!r} detectado={_nd(val_det)}"

    if diferencias:
        _log.error("Validación FB60 Pago fallida:")
        for k, v in detectados.items():
            _log.error("  %s: %r", k, v)
    else:
        _log.info(
            "Validación OCR FB60 Pago OK — Vía pago: %r",
            detectados.get("Vía pago"),
        )

    return {
        "detectados": detectados,
        "diferencias": diferencias,
        "valido": len(diferencias) == 0,
    }


# ============================================================
# [M2] BENCHMARK — métricas antes/después en tu equipo
# ============================================================

def benchmark(pantalla="1", repeticiones=3):
    """
    Mide el tiempo de la lectura OCR completa N veces.
    La 1a corrida es "fría"; las siguientes muestran el efecto del
    cache diferencial (si la pantalla no cambia, deben bajar a <0.5 s).
    """
    lectores = {
        "1": leer_valores_fb60,
        "2": leer_valores_zfiec015,
        "3": leer_valores_fb60_detalle,
    }
    lector = lectores.get(pantalla, leer_valores_fb60)

    print(f"\nBenchmark pantalla [{pantalla}] — {repeticiones} corridas")
    print(f"  mss={_HAS_MSS}  cv2={_HAS_CV2}  numpy={_HAS_NUMPY}  "
          f"workers={CONFIG['ocr_workers']}")

    tiempos = []
    for i in range(repeticiones):
        _CACHE_PANTALLA.invalidar() if i == 0 else None
        t0 = time.perf_counter()
        lector()
        dt = time.perf_counter() - t0
        tiempos.append(dt)
        print(f"  corrida {i + 1}: {dt:6.2f} s"
              + ("  (fría)" if i == 0 else "  (cache diferencial activo)"))

    print(f"  mejor: {min(tiempos):.2f} s | promedio: "
          f"{sum(tiempos) / len(tiempos):.2f} s\n")
    return tiempos


# ============================================================
# MAIN (formato de salida IDÉNTICO al original)
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from dotenv import load_dotenv
    load_dotenv(_BASE_DIR / ".env")

    if "--benchmark" in sys.argv:
        idx = sys.argv.index("--benchmark")
        pant = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else "1"
        input("Deja SAP visible y presiona Enter para iniciar el benchmark...")
        benchmark(pantalla=pant)
        sys.exit(0)

    print()
    print("======================================")
    print("LECTOR VALORES SAP — OCR")
    print("======================================")
    print()
    print("  [1]  FB60         — Registrar factura (Datos básicos)")
    print("  [2]  ZFIEC015     — Recepción de documentos Electrónicos")
    print("  [3]  FB60 Detalle — Txt.cabec. (pestaña Detalle)")
    print("  [4]  FB60 Pago    — Vía pago (pestaña Pago)")
    print("  [5]  Cerrar FB60  — Prueba _cerrar_fb60_forzado() (popup 'Fin tratamiento')")
    print()

    modo = input("Selecciona pantalla [1/2/3/4/5]: ").strip()

    if modo == "5":
        # Al ejecutar este archivo directamente (python transactions/validacion_pantalla.py),
        # sys.path[0] es la carpeta transactions/, no la raíz del proyecto — hay que
        # agregar la raíz para poder importar transactions.fb60_kb (que a su vez hace
        # `import sap_gui`, ubicado en la raíz).
        if str(_BASE_DIR) not in sys.path:
            sys.path.insert(0, str(_BASE_DIR))
        from transactions.fb60_kb import _cerrar_fb60_forzado

        print()
        print("Deja FB60 abierto en pantalla, con el popup 'Fin tratamiento'")
        print("visible (o listo para dispararse con F12).")
        print()
        input("Presiona Enter para probar _cerrar_fb60_forzado()...")
        print("Haz clic en la pantalla de SAP — 2 segundos...")
        time.sleep(2)

        ok = _cerrar_fb60_forzado()
        print()
        print("✅ FB60 cerrado correctamente" if ok else "❌ No se pudo cerrar FB60")
        input("Enter para salir...")
        sys.exit(0)

    print()
    print("Deja SAP visible en la pantalla correcta.")
    print("La consola NO debe tapar SAP.")
    print()

    input("Presiona Enter para capturar en 2 segundos...")

    print("Capturando...")
    time.sleep(2)

    try:
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

        elif modo == "4":
            resultado = leer_y_validar_fb60_pago()
            valores = resultado["detectados"]
            json_out = "(.env VIA_PAGO)"

            print()
            print("==============================")
            print("DIFERENCIAS FB60 PAGO")
            print("==============================")

            if resultado["diferencias"]:
                for k, v in resultado["diferencias"].items():
                    print(f"  {k}: {v}")
            else:
                print("  ✅ Vía pago correcto")

            print()
            print("==============================")
            print("VALORES FB60 PAGO")
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
        print(METRICAS.reporte())
        print()

    except Exception as e:
        _log.exception("Fallo inesperado durante la lectura/validación")
        print(f"\nERROR: {e}")
        print("Sugerencias: verifica que SAP esté visible, que la región")
        print("SAP_WIN_* del .env sea correcta y que Tesseract funcione")
        print("(verificar_tesseract()).")

    input("Enter para salir...")
