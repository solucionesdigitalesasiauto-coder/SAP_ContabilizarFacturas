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
pip install pywin32 python-dotenv pyautogui pyperclip pynput

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
├── coordenadas.py              # Tab-counts calibrados con Au3Info (17-18/06/2026)
├── bancos.json                 # Configuración de los 5 bancos (nombre, proveedor, textos)
├── .env                        # Credenciales y parámetros (NO subir a repositorio)
├── requirements.txt            # pywin32, python-dotenv, pyautogui, pyperclip, pynput
├── VERSION                     # Número de versión actual
├── correos/
│   ├── outlook_notifier.py     # Envío de correo (Microsoft Graph API / OAuth2)
│   ├── notificador_sap.py      # notify_resumen_banco() y notify_error_banco()
│   └── DOCS_ENVIO_CORREO.md    # Documentación del módulo de correo
├── transactions/
│   ├── zfiec015_kb.py          # ZFIEC015: llenar formulario + iterar grilla
│   └── fb60_kb.py              # FB60: registrar factura (modo prueba o real, ver .env)
├── diagnostico/
│   └── campos.py               # IDs de elementos SAP para scripting (importado por zfiec015_kb)
└── release/
    ├── build_release.bat       # ★ Script para generar el ejecutable .exe
    ├── build.py                # Lógica de build con PyInstaller
    └── combancos.spec          # Spec file de PyInstaller
```

---

## Bancos configurados (`bancos.json`)

| Banco | Proveedor SAP | Txt.cabec | Texto comisión |
|-------|--------------|-----------|----------------|
| Austro | 1000004754 | BANCO DEL AUSTRO | comision banco del austro |
| Pacifico | *(pendiente)* | BANCO DEL PACIFICO | comision banco del pacifico |
| Diners | *(pendiente)* | BANCO DINERS | comision banco diners |
| Internacional | *(pendiente)* | BANCO INTERNACIONAL | comision banco internacional |
| Guayaquil | *(pendiente)* | BANCO DE GUAYAQUIL | comision banco de guayaquil |

---

## Parámetros en `.env`

| Variable | Valor ejemplo | Uso |
|----------|--------------|-----|
| SAP_USUARIO | — | Login SAP |
| SAP_PASSWORD | — | Login SAP |
| SAP_MANDANTE | 600 | Mandante en pantalla de login |
| SAP_IDIOMA | ES | Idioma |
| SAP_SOCIEDAD | 2000 | Campo Sociedad en ZFIEC015 |
| CENTRO_COSTO | 2047001103 | Posición FB60 — columna Cen |
| CUENTA_MAYOR | 8110200002 | Posición FB60 — Cta.mayor |
| VIA_PAGO | T | Pestaña Pago — Vía pago |
| INDICADOR_IMPUESTO | B2 | IVA Compras 15% Crédito |
| TIPO_DOC_ZFIEC | 01 | Tipo de Documento en ZFIEC015 |
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
9. `_llenar_pestana_detalle()` — Ctrl+Shift+AvPág → 1 Tab → `_pegar(texto_cabecera)`
10. `_contabilizar_o_cancelar()`:
    - **Modo prueba** (`CONTABILIZAR=0`): F12 → Enter (Sí tiene foco por defecto en popup de abandono — NO usar Tab antes)
    - **Modo real** (`CONTABILIZAR=1`): Ctrl+S → espera activa (40×0.2s) → captura Nº Doc

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
| Espera tras contabilizar | Espera activa `titulo_actual()` cada 0.2s (máx 8s) — no `sleep` fijo |
| Error en un banco | `continue` en el loop — el proceso sigue con el siguiente banco |
| Notificaciones de error | `_notificar_error()` en `main.py` — loguea + correo sin cortar el flujo |

---

## Tab-counts calibrados (`coordenadas.py`)

Calibrados con Au3Info el 17-18/06/2026 en resolución actual. Si cambia monitor o resolución, recalibrar con `diagnostico/campos.py`.

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

**REGLA CRÍTICA: dos rutas completamente independientes. No mezclar.**

| Caso | Secuencia | Motivo |
|------|-----------|--------|
| `fila_idx == 0` (primera fila) | `F2 → Home → Right → Enter → Enter` | Grid recién cargado: SAP no asigna foco de teclado hasta interacción. F2 despierta el grid sin abrir transacción. Home → MIRO, Right → FB60 |
| `fila_idx > 0` (siguientes filas, modo prueba) | `Down → Home → Right → Enter → Enter` | Grid ya activo desde fila_idx=0. F2 sobre columna FB60 abriría transacción incorrecta. Solo avanzar fila con Down, luego Home → MIRO, Right → FB60 |

- `Home` con grid activo queda en **MIRO** (primera col interactiva — SAP omite MSTAT con teclado).
- `Right` (1 vez) desde MIRO → **FB60**.
- `Enter` × 2: primero abre popup de confirmación, segundo confirma Sí.
- Timeout único de 3s — si no aparece "Registrar factura" en 3s, retorna False y el loop hace break (grilla vacía en modo teclado).

---

## Estado actual (23/06/2026)

- Flujo end-to-end funcional y verificado en **producción**: login → ZFIEC015 → FB60 (todos los campos) → contabilización real (Ctrl+S)
- Todo teclado SAP via **pynput** excepto Ctrl+/ (único en pyautogui)
- Código limpio: funciones de una responsabilidad, sin pyautogui en transactions/
- Errores por banco: loguean con `exc_info=True`, envían correo y continúan (`continue`, no `break`)
- SAP se cierra automáticamente al final (`cerrar_sap()` con `/nend`)
- Espera activa tras contabilizar (40×0.2s) en lugar de sleep fijo
- Menú interactivo al inicio para elegir cantidad de docs y modo (prueba/real) sin editar .env
- `_posicion_normal` funciona para primer Y subsecuentes ingresos FB60 (verificado en producción 23/06/2026)

---

## Para habilitar contabilización real

Usar el menú interactivo al ejecutar y elegir opción `[2] Real`. También se puede forzar desde `.env`:
```
CONTABILIZAR=1
```
El menú sobreescribe el valor del `.env` si se selecciona una opción distinta.

---

## Pendientes

- [ ] Completar números de proveedor SAP para Pacífico, Diners, Internacional, Guayaquil en `bancos.json`
- [ ] Verificar columna de estado en grilla (`MSTAT`) para filtrar filas ya procesadas
- [ ] Rebuild exe con `release/build.py` tras cualquier cambio de código
