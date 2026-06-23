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


class NotificadorSAP:
    """Wrapper de OutlookNotifier con plantillas específicas para FB60 / SAP."""

    def __init__(self) -> None:
        self._n = OutlookNotifier()
        self._n.subject_prefix  = os.getenv("OUTLOOK_SUBJECT_PREFIX",  "[SAP]")
        self._n.system_name     = os.getenv("OUTLOOK_SYSTEM_NAME",     "Robot SAP Comisiones Bancarias")
        self._n.system_subtitle = os.getenv("OUTLOOK_SYSTEM_SUBTITLE", "Automatización FI — Comisiones Bancarias")

    # ── RESUMEN POR BANCO ──────────────────────────────────────────────────────

    def notify_resumen_banco(self, banco: str, registros: list[dict]) -> None:
        """
        Envía resumen al terminar el procesamiento de un banco.

        Cada dict en registros debe tener:
            numero_doc, fecha, importe, cuenta_mayor, centro_costo,
            estado ("CONTABILIZADO" | "ERROR" | "OMITIDO"), detalle
        """
        total      = len(registros)
        contabiliz = sum(1 for r in registros if r.get("estado") == "CONTABILIZADO")
        con_error  = total - contabiliz
        color      = "#27ae60" if con_error == 0 else "#e67e22"
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
              <span style="font-size:26px;font-weight:bold;color:#27ae60;">{contabiliz}</span>
            </td>
            <td style="padding:12px 20px;border-bottom:1px solid #e8e8e8;border-left:1px solid #e8e8e8;">
              <span style="font-size:13px;color:#888;">Con error</span><br>
              <span style="font-size:26px;font-weight:bold;color:#c0392b;">{con_error}</span>
            </td>
          </tr>
        </table>
        """

        def _badge(estado: str) -> str:
            cfg = {
                "CONTABILIZADO": ("#27ae60", "#eafaf1"),
                "ERROR":         ("#c0392b", "#fdecea"),
                "OMITIDO":       ("#888",    "#f5f5f5"),
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
            f"{str(r.get('detalle', ''))[:80]}{'…' if len(str(r.get('detalle', ''))) > 80 else ''}</td>"
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
        """Notifica un error crítico que impidió procesar el banco."""
        self._n.notify_critical(
            error_msg=error,
            batch_label=banco,
            description=(
                f"El proceso de comisiones bancarias fue interrumpido al intentar "
                f"procesar <strong>{banco}</strong>. Ninguna factura fue contabilizada. "
                f"Se requiere revisión manual en SAP (transacciones ZFIEC015 / FB60)."
            ),
        )
