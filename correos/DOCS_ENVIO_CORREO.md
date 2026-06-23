# Documentación: Envío de Correos en el Robot RPA

## Tecnología utilizada

El robot envía correos usando **Microsoft Outlook** a través de la librería COM de Windows:

```python
import win32com.client as win32
```

**Requisito:** Outlook debe estar instalado y con sesión iniciada en la máquina donde corre el robot.

---

## Configuración de destinatarios

Los correos se configuran en **`configuracion_robot.ini`** (sección `[CORREOS]`):

```ini
[CORREOS]
correos_para = usuario@empresa.com
correos_cc   = usuario2@empresa.com
```

Ese archivo es leído por **`config.py`** y expone las siguientes variables globales:

| Variable en config.py       | Uso                                |
|-----------------------------|------------------------------------|
| `CORREOS_SEMANAL_PARA`      | Destinatario del reporte semanal   |
| `CORREOS_SEMANAL_CC`        | CC del reporte semanal             |
| `CORREOS_ALERTAS_PARA`      | Destinatario de alertas diarias    |
| `CORREOS_ALERTAS_CC`        | CC de alertas diarias              |
| `CORREOS_SEMAFORO_PARA`     | Destinatario de alertas semáforo   |
| `CORREOS_SEMAFORO_CC`       | CC de alertas semáforo             |
| `CORREOS_DIARIO_PARA`       | Destinatario de reporte diario     |
| `CORREOS_DIARIO_CC`         | CC de reporte diario               |

Todas las variables `_PARA` y `_CC` apuntan al mismo valor leído del `.ini` —
cambiar `correos_para` y `correos_cc` en el `.ini` actualiza todos a la vez.

---

## Patrón estándar de envío (idéntico en todos los módulos)

```python
import win32com.client as win32
import config

def enviar_correo(adjuntos: list[str], asunto: str, cuerpo_html: str):
    correo_para = config.CORREOS_ALERTAS_PARA   # o la variable que corresponda
    correo_cc   = config.CORREOS_ALERTAS_CC

    outlook = win32.Dispatch('outlook.application')
    mail = outlook.CreateItem(0)        # 0 = MailItem
    mail.To = correo_para
    if correo_cc:
        mail.CC = correo_cc
    mail.Subject = asunto
    mail.HTMLBody = cuerpo_html         # Cuerpo en HTML

    for adjunto in adjuntos:
        mail.Attachments.Add(os.path.abspath(adjunto))  # ruta absoluta obligatoria

    mail.Send()
```

---

## Dónde está implementado cada tipo de correo

| Tipo de correo            | Función                         | Archivo                  | Línea aprox. |
|---------------------------|---------------------------------|--------------------------|--------------|
| Reporte semanal           | `generar_resumen_semanal()`     | `reporte_semanal.py`     | 163          |
| Alerta diaria (proveedores nuevos/en proceso) | `enviar_alerta_diaria()` | `actualizador_total.py` | 288  |
| Alerta semáforo de pago   | `enviar_correo_semaforo()`      | `actualizador_total.py`  | 404          |

---

## Cuerpo HTML — estructura tipo

Todos los correos usan HTML inline con estilos básicos de Calibri/Arial:

```html
<html>
<body style="font-family: Calibri, Arial, sans-serif; font-size: 11pt;">
    <p>Estimado equipo,</p>
    <p>Mensaje del robot...</p>
    <p>Este mensaje ha sido generado automáticamente
    por el Robot RPA de Finanservices S.A.S.</p>
</body>
</html>
```

El campo `mail.HTMLBody` acepta HTML completo.  
No se usa `mail.Body` (texto plano) — siempre `HTMLBody`.

---

## Adjuntos

- Se adjuntan archivos Excel (`.xlsx`) generados en tiempo de ejecución.
- La ruta debe ser **absoluta** — usar `os.path.abspath(ruta)`.
- El reporte semanal adjunta 1 archivo; la alerta diaria puede adjuntar varios (loop).
- Algunos correos eliminan el archivo temporal después de enviarlo (`os.remove`).

---

## Condiciones de ejecución por tipo

| Tipo                  | Condición de disparo                                              |
|-----------------------|-------------------------------------------------------------------|
| Reporte semanal       | Solo si `datetime.now().weekday() == 0` (lunes)                  |
| Alerta diaria         | Si hay proveedores NO EVALUADOS o EN PROCESO en el maestro       |
| Alerta semáforo       | Si hay facturas con semáforo ROJO, NARANJA o AMARILLO en el mes  |

---

## Dependencias requeridas

```
pywin32          # pip install pywin32  → provee win32com.client
openpyxl         # para formatear el Excel adjunto
pandas           # para leer/procesar el maestro Excel
```

Microsoft Outlook **debe estar instalado** en la máquina — no funciona con webmail.

---

## Errores comunes

| Error                                        | Causa probable                              |
|----------------------------------------------|---------------------------------------------|
| `pywintypes.com_error`                       | Outlook no está abierto o no está instalado |
| Correo enviado pero sin adjunto              | La ruta del adjunto no era absoluta         |
| `KeyError` al leer config                    | Faltan las claves en `configuracion_robot.ini` |
| Correo llega con cuerpo vacío               | Se asignó `mail.Body` en lugar de `mail.HTMLBody` |
