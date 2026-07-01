# SAP Automatización – Comisiones Bancarias
**Empresa:** ASIAUTO S.A.  
**Módulo SAP:** FI – Cuentas por Pagar  
**SAP GUI:** 800 Final Release 64-bit  
**Sistema:** PS4 PRODUCCION (172.50.6.37) | Mandante 600 | Sociedad 2000

---

## Objetivo

Automatizar el proceso mensual de recepción y contabilización de facturas de comisiones bancarias:
1. Buscar documentos electrónicos pendientes por banco en **ZFIEC015**
2. Registrar cada factura en **FB60** con los datos contables correctos

---

## Cómo ejecutar

```bash
# 1. Instalar dependencias (solo la primera vez)
pip install pywin32 python-dotenv pyautogui pyperclip pynput pywinauto

# 2. Llenar credenciales en .env (ver tabla de parámetros más abajo)

# 3. Ejecutar (SAP puede estar cerrado — el script lo abre)
python main.py
```

---

## Archivos del proyecto

```
SAP/
├── main.py                     # ★ PUNTO DE ENTRADA — menú interactivo multi-banco
├── sap_gui.py                  # Motor de automatización (teclado/mouse: pynput + pyautogui)
├── bancos.json                 # Configuración de los 7 bancos (nombre, proveedor, cuenta_mayor, textos)
├── .env                        # Credenciales y parámetros (NO subir a repositorio)
├── requirements.txt            # pywin32, python-dotenv, pyautogui, pyperclip, pynput, pywinauto, pillow, pytesseract
├── VERSION                     # Número de versión actual
├── correos/
│   ├── outlook_notifier.py     # Envío de correo (Microsoft Graph API / OAuth2)
│   ├── notificador_sap.py      # notify_resumen_banco() y notify_error_banco()
│   └── DOCS_ENVIO_CORREO.md    # Documentación del módulo de correo
├── transactions/
│   ├── zfiec015_kb.py          # ZFIEC015: llenar formulario + iterar grilla
│   ├── fb60_kb.py              # FB60: registrar factura (modo prueba o real, ver .env)
│   └── validacion_Pantalla.py  # OCR via Tesseract: valida pantallas ZFIEC015 y FB60 antes de ejecutar
├── diagnostico/
│   └── campos.py               # IDs de elementos SAP para scripting (importado por zfiec015_kb)
└── release/
    ├── build_release.bat       # ★ Script para generar el ejecutable .exe
    ├── build.py                # Lógica de build con PyInstaller
    └── combancos.spec          # Spec file de PyInstaller (incluye Tesseract OCR auto-contenido)
```

---

## Bancos configurados (`bancos.json`)

| Orden | Banco | Proveedor SAP | Cta.mayor GL | Txt.cabec | Texto comisión |
|-------|-------|--------------|--------------|-----------|----------------|
| 1 | Austro | 1000004754 | 8110200002 | BANCO DEL AUSTRO | COMISION BANCO AUSTRO |
| 2 | Guayaquil | 1000001661 | 8110200007 | BANCO DE GUAYAQUIL | COMISION BANCO GUAYAQUIL |
| 3 | Pacifico | 1000006932 | 8110200003 | BANCO DEL PACIFICO | COMISION BANCO PACIFICO |
| 4 | Pichincha | 1000004511 | 8110200004 | BANCO PICHINCHA | COMISION BANCO PICHINCHA |
| 5 | Diners | 1000004516 | 8110200021 | BANCO DINERS | COMISION BANCO DINERS |
| 6 | Bolivariano | 1000083356 | 8110200008 | BANCO BOLIVARIANO | COMISION BANCO BOLIVARIANO |
| 7 | Internacional | 1000023397 | 8110200009 | BANCO INTERNACIONAL | COMISION BANCO INTERNACIONAL |

Cada banco tiene su propia `cuenta_mayor` GL — se lee desde `bancos.json`, **no** desde `.env`.

---

## Parámetros en `.env`

| Variable | Valor ejemplo | Uso |
|----------|--------------|-----|
| SAP_USUARIO | — | Login SAP |
| SAP_PASSWORD | — | Login SAP |
| SAP_MANDANTE | 600 | Mandante en pantalla de login |
| SAP_IDIOMA | ES | Idioma |
| SAP_SOCIEDAD | 2000 | Campo Sociedad en ZFIEC015 |
| CENTRO_COSTO | 2047001103 | Fallback centro costo (cada banco lo define en `bancos.json`) |
| CUENTA_MAYOR | *(ya no se usa)* | Reemplazado por `cuenta_mayor` en `bancos.json` por banco |
| VIA_PAGO | T | Pestaña Pago — Vía pago |
| INDICADOR_IMPUESTO | B2 | IVA Compras 15% Crédito |
| TIPO_DOC_ZFIEC | 01 | Tipo de Documento en ZFIEC015 |
| MES_ANTERIOR | 0 | `0`=mes actual, `1`=mes anterior. Determina el período de búsqueda en ZFIEC015. |
| CONTABILIZAR | 0 | Valor inicial (`0`=prueba, `1`=real). **El menú interactivo lo sobreescribe en tiempo de ejecución.** |
| MAX_DOCS_BANCO | 1 | Valor inicial de documentos por banco. **El menú interactivo lo reemplaza; solo aplica si se omite el menú.** |
| SAP_WIN_X / Y | 0 / 0 | Posición de ventana SAP al iniciar |
| SAP_WIN_ANCHO / ALTO | 1024 / 768 | Tamaño de ventana SAP |

---

## Flujo FB60 por factura (`fb60_kb.py`)

1. Esperar y activar ventana "Registrar factura", posicionar en (0,0)
2. `_copiar_fecha_factura()` — 2 tabs → Fecha Factura → `Ctrl+A+C` → portapapeles
3. `_llenar_fecha_contabilizacion()` — 2 tabs más → pegar con `pegar_fecha()`
4. `_marcar_calc_impuestos()` — 5 tabs → Space → checkbox Calc.Impuestos
5. `_ingresar_indicador_impuesto()` — foco inmediato en B2 → escribir → Tab → Enter
6. `_llenar_posicion()` — entra tabla de posiciones:
   - Cta.mayor: `_pegar()` vía portapapeles (evita Ctrl+A que selecciona filas)
   - Importe: `*` — `_pegar("*")`
   - Texto: texto comisión del banco
   - Centro Costo: `escribir()` directo
7. `_salir_tabla_y_limpiar_advertencia()` — `salir_tabla()` (4× Ctrl+Shift+Tab) → Enter ×2
8. `_llenar_pestana_pago()` — Ctrl+Shift+AvPág → 3× Down → `_pegar(via_pago)`
9. `_llenar_pestana_detalle()` — Ctrl+Shift+AvPág → 1 Tab → `_pegar(texto_cabecera)` → 2× `pestana_anterior()` → vuelve a Datos básicos (doc 2+ abre con cursor en Acreedor)
10. `_contabilizar_o_cancelar()`:
    - **Modo prueba** (`CONTABILIZAR=0`): F12 → Enter (Sí tiene foco por defecto en popup de abandono — NO usar Tab antes)
    - **Modo real** (`CONTABILIZAR=1`): `SAP.tab(1)` → pywinauto `click_input()` en botón Contabilizar (Footer → auto_id=4004) → `_SLEEP_POPUP` (2s) → 3× Enter para cerrar popup "Información" embebido → retorna "OK"
    - Popup "Información" ("Doc.XXXX se contabilizó") es **embebido** en la ventana SAP FB60 — NO es top-level, NO detectable por win32gui.EnumWindows. Se cierra con Enter fallback.
    - `SAP.tab(1)` antes del clic es obligatorio — sin él, Txt.cabec sigue en edit mode y SAP ignora el clic.

---

## Regla clave de teclado en SAP

| Situación | Herramienta | Motivo |
|-----------|------------|--------|
| Toda tecla/combinación SAP | **pynput** (`_press`, `_kb.pressed`, etc.) | pyautogui suelta teclas demasiado rápido para el mensaje loop de SAP |
| Ctrl+/ (barra de comandos) | **pyautogui** `combo('ctrl','/')` | pynput's Ctrl+/ no activa la barra — escribe en el campo activo (Sociedad) |
| Escritura de texto libre | **pyautogui** `write()` | funciona bien para texto, no para teclas especiales |
| Mouse / clics | **pyautogui** o **win32api** | ambos funcionan |

---

## Técnicas de automatización

| Problema | Solución |
|----------|----------|
| Limpieza de campos en ZFIEC015 | `Ctrl+A + Delete` (`campo_ctrlA`) — Home navega fuera del campo en ese formulario |
| Limpieza de campo dentro de tabla FB60 | `_pegar()` vía portapapeles — `limpiar()` usa Home que salta a primera columna |
| Salir de tabla de posiciones | `salir_tabla()`: 4× Ctrl+Shift+Tab (pynput) |
| Cambiar pestaña FB60 | `siguiente_pestana()`: Ctrl+Shift+AvPág (pynput) — solo fuera de la tabla |
| Popup Sí/No | NO llamar `activar()` antes del Enter — quitaría el foco del popup |
| Grilla ALV (mover cursor Right) | pynput `_kbd.press(Key.right)` — pyautogui.press('right') SAP lo ignora |
| Foco en ventana | `activar()` — solo restaura si minimizada, no redimensiona maximizada |
| Cierre de SAP | `cerrar_sap()`: `/nend` + Enter en popup |
| Espera tras contabilizar | `_SLEEP_POPUP=2s` fijo + 3× Enter → popup embebido se cierra con pynput Enter |
| Error en un banco | `continue` en el loop — el proceso sigue con el siguiente banco |
| Grilla vacía (sin docs) | `procesar_documentos` retorna `([], [])` si "recepci" en título post-`_abrir_fb60_teclado` — NO lanza RuntimeError |
| Notificaciones de error | `_notificar_error()` en `main.py` — loguea + correo sin cortar el flujo. Grilla vacía NO es error. |
| pywinauto botón SAP | `click_input()` funciona; `invoke()` (UIA InvokePattern) NO activa guardado en SAP |

---

## Tab-counts calibrados

### ZFIEC015 — tabs desde campo Sociedad
| Campo | Tabs |
|-------|------|
| Proveedor | 1 |
| Fecha desde | 9 |
| Fecha hasta | 1 |
| Tipo Doc | 5 |
| Radio Pendiente | 4 |

### FB60 cabecera — tabs desde campo Acreedor
| Campo | Tabs acumulados |
|-------|----------------|
| Fecha Factura | 2 |
| Fecha Contabilización | 4 (total desde Acreedor) |
| Calc.Impuestos | 5 (total desde Acreedor) |
| Indicador impuesto (B2) | 0 (foco inmediato tras Space) |

### FB60 tabla de posiciones — una sola ruta `_posicion_normal`

Verificado en producción 23/06/2026: `_posicion_normal` funciona para primer ingreso Y subsecuentes.

| Secuencia hasta Cta.mayor |
|--------------------------|
| `Tab(1) → Down → Ctrl+Shift+Tab → Tab(2) → pegar` |

`_llenar_posicion` llama siempre a `_posicion_normal` (el parámetro `primer_ingreso` se conserva en la firma para compatibilidad pero no se usa).

Campos comunes (función `_llenar_resto_posicion`):
| Campo | Tabs desde Cta.mayor |
|-------|---------------------|
| Importe | 2 |
| Texto | 6 |
| Centro Costo | 5 |

### FB60 — pestaña Pago
| Campo | Descripción |
|-------|------------|
| Vía pago | 3× Down desde primer campo de la pestaña |

### FB60 — pestaña Detalle
| Campo | Tabs desde entrar a pestaña |
|-------|----------------------------|
| Txt.cabec | 1 |

---

## Notificaciones por correo (`correos/`)

- `outlook_notifier.py` — Microsoft Graph API (OAuth2 client_credentials). Se deshabilita automáticamente si las credenciales son placeholder.
- `notificador_sap.py` — dos funciones: `notify_resumen_banco(banco, registros)` y `notify_error_banco(banco, error)`.
- Correo de Azure: credenciales en `correos/.env.privado` — **NO copiar** a output de build.
- En `main.py`: `_notificar()` y `_notificar_error()` envuelven las llamadas — si el correo falla, solo registra `warning` en el log, sin cortar el proceso.

---

## Menú interactivo (`_menu_opciones` en `main.py`)

Aparece después de mostrar bancos y período, antes de abrir SAP. Permite elegir sin editar el `.env`:

```
  Documentos a procesar por banco:
    [1]  1  documento
    [2]  2  documentos
    [3]  10 documentos  (bloque)
    [4]  Todos          (sin límite)

  Modo de ejecución:
    [1]  Prueba  — verifica campos sin guardar (F12)
    [2]  Real    — contabiliza en SAP          (Ctrl+S)
```

- La opción de documentos determina `max_docs` pasado a `procesar_documentos()`.
  - `0` / Todos → `max_docs = None` → itera toda la grilla sin límite.
  - N → para al completar N facturas por banco.
- La opción de modo sobreescribe `os.environ["CONTABILIZAR"]` en tiempo de ejecución;
  `fb60_kb._contabilizar_o_cancelar()` lo lee con `os.getenv("CONTABILIZAR", "0")`.

---

## Navegación de grilla ZFIEC015 en modo teclado (`_abrir_fb60_teclado`)

**Siempre se llama con `fila_idx=0`** — SAP refresca la grilla tras cada contabilización y el siguiente doc queda en row 0.

| Secuencia | Detalle |
|-----------|---------|
| `F2 (1s) → Home (0.4s) → Right (0.4s) → Enter` | F2 activa foco del grid. Home → columna MIRO. Right → columna FB60. Enter abre popup HTML de confirmación. |
| Retry Enter × `_MAX_INTENTOS_POPUP=3` cada 1.5s | El popup HTML de ZFIEC015 tarda variable (1-8s) en renderizar. Se reintenta Enter hasta ver título FB60. |

- `Home` queda en **MIRO** (SAP omite MSTAT con teclado).
- `Right` (1 vez) desde MIRO → **FB60**.
- Después de `_abrir_fb60_teclado` para doc 2+: `time.sleep(1.0)` antes de `registrar_factura`.
- F2 necesita 1.0s de espera (era 0.5s) — timing insuficiente causaba que Right no registrara.
- Si FB60 no abre en `_TIMEOUT_FB60=5s` y título sigue en ZFIEC015 → grilla vacía → `return ([], [])`.
- Si título es otra pantalla → `RuntimeError` → email de error.

---

## Estado actual (30/06/2026) — `v1.0.1` (tag git actualizado)

- Flujo end-to-end funcional y verificado en **producción**: login → ZFIEC015 → FB60 (todos los campos) → contabilización real (pywinauto click_input + Enter) → múltiples documentos y múltiples bancos
- Todo teclado SAP via **pynput** excepto Ctrl+/ (único en pyautogui). pywinauto para botón Contabilizar.
- `_contabilizar()`: click_input() → 2s sleep → 3× Enter cierra popup "Información" embebido
- Grilla vacía → retorno limpio `([], [])`, sin error ni correo
- Todos los sleeps y timeouts en bloques `_SLEEP_*` / `_TIMEOUT_*` al inicio de cada archivo — sin magic numbers en funciones
- `_MAX_INTENTOS_POPUP=3` en zfiec015_kb para retry del popup HTML de ZFIEC015
- `_TIMEOUT_POPUP_CONFIRM=2.0s` en zfiec015_kb — detecta popup por pantalla via pywinauto antes del Enter fallback
- Errores por banco: loguean con `exc_info=True`, envían correo y continúan (`continue`, no `break`)
- SAP se cierra automáticamente al final (`cerrar_sap()` con `/nend`)
- Menú interactivo al inicio para elegir cantidad de docs y modo (prueba/real) sin editar .env
- `_posicion_normal` funciona para primer Y subsecuentes ingresos FB60 (verificado en producción)
- `_llenar_pestana_detalle()` regresa a Datos básicos con 2× `pestana_anterior()` — doc 2+ abre con cursor en Acreedor
- `_llenar_resto_tabla()`: pre-sleep `_SLEEP_MEDIO` antes de `activar()` en los tres campos (fix máquina rápida: Ctrl+V llega con foco asentado)

### Cambios 30/06/2026

- **Fix multi-banco**: `cuenta_mayor` GL se lee de `bancos.json` por banco — antes tomaba `CUENTA_MAYOR` del `.env` (valor fijo de Austro) para todos los bancos
- **Log OCR detallado**: `posicion_normal` loguea `imp=` y `txt=`; validación FB60 OK imprime todos los valores detectados campo a campo
- **Validación OCR**: `validacion_Pantalla.py` incluido en el build — antes faltaba en el spec y se omitía silenciosamente
- **Build auto-contenido**: Tesseract OCR (exe + DLLs + tessdata) empaquetado en `ComBancos.exe` — segunda máquina no necesita instalar nada
- **Paths de runtime correctos en .exe**: logs, screenshots, `valores_bancos.json` y `valores_fb60.json` se crean junto al `.exe` (antes iban a `sys._MEIPASS`, carpeta temporal que desaparece al cerrar)

---

## Para habilitar contabilización real

Usar el menú interactivo al ejecutar y elegir opción `[2] Real`. También se puede forzar desde `.env`:
```
CONTABILIZAR=1
```
El menú sobreescribe el valor del `.env` si se selecciona una opción distinta.

---

## Reglas de trabajo con Claude

- **Usar `Edit` siempre**, nunca `Write` para archivos existentes — solo el fragmento que cambia
- **Una función a la vez**: si el bug está en `procesar_documentos`, solo tocar esa función hasta que el usuario confirme que funciona
- **Marcar con `# TODO: fix`** la línea exacta que falla al inicio del debug; limpiar solo al confirmar
- No releer archivos que no van a cambiar
- No tocar funciones fuera del scope del bug reportado

---

## Pendientes

*(ninguno)*
