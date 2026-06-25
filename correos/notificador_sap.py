"""
correos/notificador_sap.py
==========================
Notificaciones de correo para el proceso de comisiones bancarias SAP.

Uso:
    from correos.notificador_sap import NotificadorSAP

    notif = NotificadorSAP()

    notif.notify_resumen_banco(
        banco="Banco del Austro",
        registros=[
            {
                "numero_doc":   "1800012345",
                "fecha":        "22.06.2026",
                "importe":      "1,234.56",
                "cuenta_mayor": "8110200002",
                "centro_costo": "2047001103",
                "estado":       "CONTABILIZADO",
                "detalle":      "",
            },
        ],
    )

    notif.notify_error_banco("Banco del Austro", str(exc))

Variables de entorno (.env + correos/.env.privado):
    OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET, OUTLOOK_TENANT_ID
    OUTLOOK_SENDER_EMAIL    — debe ser un usuario del tenant Azure AD
    OUTLOOK_RECIPIENT_EMAIL — uno o varios separados por coma
    OUTLOOK_SUBJECT_PREFIX, OUTLOOK_SYSTEM_NAME, OUTLOOK_COMPANY_NAME
    OUTLOOK_SYSTEM_SUBTITLE, OUTLOOK_FOOTER_TEXT
"""
from __future__ import annotations

import os
from correos.outlook_notifier import OutlookNotifier

# ── Colores de estado en tabla de registros ───────────────────
_COLOR_CONTABILIZADO_FG = "#27ae60"
_COLOR_CONTABILIZADO_BG = "#eafaf1"
_COLOR_ERROR_FG         = "#c0392b"
_COLOR_ERROR_BG         = "#fdecea"
_COLOR_OMITIDO_FG       = "#888"
_COLOR_OMITIDO_BG       = "#f5f5f5"

# ── Límite de caracteres en columna Detalle ───────────────────
_MAX_DETALLE = 80   # caracteres máximos mostrados en columna Detalle del correo


class NotificadorSAP:
    """Wrapper de OutlookNotifier con plantillas específicas para FB60 / SAP."""

    def __init__(self) -> None:
        """Inicializa el notificador con configuración específica de comisiones bancarias.

        Sobreescribe los valores por defecto de OutlookNotifier con los del .env
        para mostrar el nombre y subtítulo correctos en los correos.

        Hardcoded:
            - "[SAP]": prefijo por defecto si no hay .env (CONFIG)
            - "Robot SAP Comisiones Bancarias": nombre del sistema (CONFIG)
            - "Automatización FI — Comisiones Bancarias": subtítulo (CONFIG)
        """
        self._n = OutlookNotifier()
        self._n.subject_prefix  = os.getenv("OUTLOOK_SUBJECT_PREFIX",  "[SAP]")
        self._n.system_name     = os.getenv("OUTLOOK_SYSTEM_NAME",     "Robot SAP Comisiones Bancarias")
        self._n.system_subtitle = os.getenv("OUTLOOK_SYSTEM_SUBTITLE", "Automatización FI — Comisiones Bancarias")

    # ── RESUMEN POR BANCO ──────────────────────────────────────────────────────

    def notify_resumen_banco(self, banco: str, registros: list[dict]) -> None:
        """Envía resumen al terminar el procesamiento de un banco.

        Genera una tabla HTML con estadísticas (total / contabilizadas / con error)
        y el detalle de cada registro con su estado en badge de color.

        Args:
            banco (str): Nombre del banco procesado (ej. "Banco del Austro").
            registros (list[dict]): Lista de registros procesados. Cada dict debe tener:
                - numero_doc (str): Número de documento SAP.
                - fecha (str): Fecha de la factura en formato SAP.
                - importe (str): Importe de la factura.
                - cuenta_mayor (str): Cuenta mayor GL usada.
                - centro_costo (str): Centro de costo asignado.
                - estado (str): "CONTABILIZADO" | "ERROR" | "OMITIDO".
                - detalle (str): Mensaje de error o detalle adicional.

        Returns:
            None

        Hardcoded:
            - _MAX_DETALLE = 80: máximo de chars en columna Detalle (CONFIG)
            - "CONTABILIZADO", "ERROR", "OMITIDO": valores de estado válidos (STRING)
            - _COLOR_*: colores de badges por estado (ESTILO)
            - CSS inline de la tabla: estilos de presentación (ESTILO)
        """
        total      = len(registros)
        contabiliz = sum(1 for r in registros if r.get("estado") == "CONTABILIZADO")
        con_error  = total - contabiliz
        color      = _COLOR_CONTABILIZADO_FG if con_error == 0 else "#e67e22"
        icono      = "✅" if con_error == 0 else "⚠️"

        stats = f"""
        <table width="100%" cellpadding="0" cellspacing="0"
               style="border-collapse:collapse;border:1px solid #e8e8e8;
                      border-radius:4px;overflow:hidden;margin-bottom:20px;">
          <tr style="background:#f8f9fa;">
            <td style="padding:12px 20px;border-bottom:1px solid #e8e8e8;">
              <span style="font-size:13px;color:#888;">Total facturas</span><br>
              <span style="font-size:26px;font-weight:bold;color:#333;">{total}</span>
            </td>
            <td style="padding:12px 20px;border-bottom:1px solid #e8e8e8;border-left:1px solid #e8e8e8;">
              <span style="font-size:13px;color:#888;">Contabilizadas</span><br>
              <span style="font-size:26px;font-weight:bold;color:{_COLOR_CONTABILIZADO_FG};">{contabiliz}</span>
            </td>
            <td style="padding:12px 20px;border-bottom:1px solid #e8e8e8;border-left:1px solid #e8e8e8;">
              <span style="font-size:13px;color:#888;">Con error</span><br>
              <span style="font-size:26px;font-weight:bold;color:{_COLOR_ERROR_FG};">{con_error}</span>
            </td>
          </tr>
        </table>
        """

        def _badge(estado: str) -> str:
            """Genera un badge HTML con el color del estado del registro.

            Args:
                estado (str): Estado del registro ("CONTABILIZADO", "ERROR", "OMITIDO").

            Returns:
                str: HTML del badge con estilo inline.
            """
            cfg = {
                "CONTABILIZADO": (_COLOR_CONTABILIZADO_FG, _COLOR_CONTABILIZADO_BG),
                "ERROR":         (_COLOR_ERROR_FG,         _COLOR_ERROR_BG),
                "OMITIDO":       (_COLOR_OMITIDO_FG,       _COLOR_OMITIDO_BG),
            }
            fg, bg = cfg.get(estado.upper(), ("#555", "#f5f5f5"))
            return (
                f"<span style='background:{bg};color:{fg};padding:2px 8px;"
                f"border-radius:4px;font-size:11px;font-weight:bold'>{estado}</span>"
            )

        filas = "".join(
            f"<tr style='border-bottom:1px solid #f0f0f0;'>"
            f"<td style='padding:8px 12px;font-family:monospace;font-size:13px;color:#333;'>{r.get('numero_doc', '—')}</td>"
            f"<td style='padding:8px 12px;font-size:13px;color:#555;'>{r.get('fecha', '')}</td>"
            f"<td style='padding:8px 12px;font-size:13px;color:#333;text-align:right;'>{r.get('importe', '')}</td>"
            f"<td style='padding:8px 12px;font-family:monospace;font-size:12px;color:#666;'>{r.get('cuenta_mayor', '')}</td>"
            f"<td style='padding:8px 12px;font-family:monospace;font-size:12px;color:#666;'>{r.get('centro_costo', '')}</td>"
            f"<td style='padding:8px 12px;'>{_badge(r.get('estado', ''))}</td>"
            f"<td style='padding:8px 12px;font-size:11px;color:#999;'>"
            f"{str(r.get('detalle', ''))[:_MAX_DETALLE]}"
            f"{'…' if len(str(r.get('detalle', ''))) > _MAX_DETALLE else ''}</td>"
            f"</tr>"
            for r in registros
        )

        tabla = f"""
        <p style="margin:0 0 10px 0;font-size:13px;color:#555;font-weight:bold;">
          Detalle de registros contabilizados en FB60
        </p>
        <table width="100%" cellpadding="0" cellspacing="0"
               style="border-collapse:collapse;border:1px solid #e8e8e8;border-radius:4px;overflow:hidden;">
          <thead>
            <tr style="background:#f8f9fa;border-bottom:2px solid #dee2e6;">
              <th style="padding:9px 12px;text-align:left;color:#555;font-size:12px;">Nº Doc. SAP</th>
              <th style="padding:9px 12px;text-align:left;color:#555;font-size:12px;">Fecha Factura</th>
              <th style="padding:9px 12px;text-align:right;color:#555;font-size:12px;">Importe</th>
              <th style="padding:9px 12px;text-align:left;color:#555;font-size:12px;">Cta. Mayor</th>
              <th style="padding:9px 12px;text-align:left;color:#555;font-size:12px;">C. Costo</th>
              <th style="padding:9px 12px;text-align:left;color:#555;font-size:12px;">Estado</th>
              <th style="padding:9px 12px;text-align:left;color:#555;font-size:12px;">Detalle</th>
            </tr>
          </thead>
          <tbody>{filas}</tbody>
        </table>
        """ if registros else "<p style='color:#888;'>Sin registros procesados.</p>"

        self._n._send_email(
            subject=f"{self._n.subject_prefix} {icono} {banco}: {contabiliz}/{total} facturas contabilizadas",
            html_body=self._n._html(
                titulo=f"{icono} {banco} — Comisiones procesadas",
                color_titulo=color,
                cuerpo=stats + tabla,
                batch_label=banco,
            ),
        )

    # ── ERROR CRÍTICO POR BANCO ────────────────────────────────────────────────

    def notify_error_banco(self, banco: str, error: str) -> None:
        """Notifica un error crítico que impidió procesar el banco.

        Envía el traceback completo y un mensaje explicativo indicando
        que se requiere revisión manual en ZFIEC015 / FB60.

        Args:
            banco (str): Nombre del banco donde ocurrió el error.
            error (str): Mensaje de error o traceback completo.

        Returns:
            None

        Hardcoded:
            - "ZFIEC015 / FB60": transacciones SAP mencionadas en el cuerpo (STRING)
        """
        self._n.notify_critical(
            error_msg=error,
            batch_label=banco,
            description=(
                f"El proceso de comisiones bancarias fue interrumpido al intentar "
                f"procesar <strong>{banco}</strong>. Ninguna factura fue contabilizada. "
                f"Se requiere revisión manual en SAP (transacciones ZFIEC015 / FB60)."
            ),
        )
