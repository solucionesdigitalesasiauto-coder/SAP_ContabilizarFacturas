# ComBancos — Automatización de Comisiones Bancarias SAP

**Empresa:** ASIAUTO S.A.  
**Módulo SAP:** FI – Cuentas por Pagar  
**Sistema:** PS4 PRODUCCION (172.50.6.37) | Mandante 600 | Sociedad 2000

---

## Qué hace

Automatiza el proceso mensual de recepción y contabilización de facturas de comisiones bancarias:

1. Abre SAP y hace login automáticamente
2. Por cada banco configurado busca documentos electrónicos pendientes en **ZFIEC015**
3. Por cada documento abre **FB60** y registra la factura con los datos contables correctos
4. Valida por OCR que los campos ingresados en pantalla sean correctos antes de contabilizar
5. Envía correo de resumen al finalizar cada banco

**Bancos soportados:** Austro · Guayaquil · Pacifico · Pichincha · Diners · Bolivariano · Internacional

---

## Ejecutar desde el .exe (segunda máquina)

Solo se necesitan dos archivos en la misma carpeta:

```
ComBancos.exe
.env
```

Doble clic en `ComBancos.exe`. El menú interactivo aparece en consola:

```
  Documentos a procesar por banco:
    [1]  1  documento
    [2]  2  documentos
    [3]  10 documentos
    [4]  Todos

  Modo de ejecución:
    [1]  Prueba  — verifica campos sin guardar
    [2]  Real    — contabiliza en SAP
```

El `.exe` incluye Tesseract OCR — no hay nada más que instalar.

---

## Archivos que genera junto al .exe

| Archivo | Contenido |
|---------|-----------|
| `sap_combancos.log` | Log completo de la ejecución |
| `screenshots\ocr_screen.png` | Última captura de pantalla analizada por OCR |
| `valores_bancos.json` | Parámetros ZFIEC015 del banco activo (runtime) |
| `valores_fb60.json` | Parámetros FB60 del banco activo (runtime) |

---

## Configuración `.env`

```env
SAP_USUARIO=tu_usuario
SAP_PASSWORD=tu_password
SAP_MANDANTE=600
SAP_IDIOMA=ES
SAP_SOCIEDAD=2000

VIA_PAGO=T
INDICADOR_IMPUESTO=B2
TIPO_DOC_ZFIEC=01

CONTABILIZAR=0        # 0=prueba, 1=real (el menú lo sobreescribe)
MAX_DOCS_BANCO=1      # el menú lo sobreescribe

SAP_WIN_X=0
SAP_WIN_Y=0
SAP_WIN_ANCHO=1024
SAP_WIN_ALTO=768
```

La `cuenta_mayor` GL de cada banco se configura en `bancos.json`, no en `.env`.

---

## Ejecutar desde Python (desarrollo)

```bash
# 1. Instalar dependencias
pip install pywin32 python-dotenv pyautogui pyperclip pynput pywinauto pillow pytesseract

# 2. Instalar Tesseract OCR
#    https://github.com/UB-Mannheim/tesseract/wiki

# 3. Llenar .env con credenciales

# 4. Ejecutar
python main.py
```

---

## Generar el ejecutable

```bash
cd release
build_release.bat
```

El `.exe` queda en `release\dist\ComBancos.exe`. Incluye Python, todas las librerías y Tesseract OCR — es auto-contenido.

---

## Estructura del proyecto

```
SAP/
├── main.py                          # Punto de entrada — menú y loop de bancos
├── sap_gui.py                       # Motor teclado/mouse (pynput + pyautogui)
├── coordenadas.py                   # Tab-counts calibrados por pantalla SAP
├── bancos.json                      # Configuración de los 7 bancos
├── .env                             # Credenciales (NO subir al repositorio)
├── transactions/
│   ├── zfiec015_kb.py               # Automatización pantalla ZFIEC015
│   ├── fb60_kb.py                   # Automatización pantalla FB60
│   └── validacion_Pantalla.py       # Validación OCR de pantallas SAP
├── correos/
│   ├── outlook_notifier.py          # Envío correo via Microsoft Graph API
│   └── notificador_sap.py           # notify_resumen_banco / notify_error_banco
├── diagnostico/
│   └── campos.py                    # IDs de elementos SAP (Au3Info)
└── release/
    ├── build_release.bat            # Script de build
    ├── build.py                     # Lógica PyInstaller
    └── combancos.spec               # Spec con Tesseract bundleado
```

---

## Versiones

| Tag | Fecha | Descripción |
|-----|-------|-------------|
| `v1.0.1` | 30/06/2026 | Producción — validación OCR FB60 + fix multi-banco + build auto-contenido |
