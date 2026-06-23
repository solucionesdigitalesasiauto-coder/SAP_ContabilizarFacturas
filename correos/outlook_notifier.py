"""
correos/outlook_notifier.py
===========================
Envío de notificaciones por correo usando win32com (Outlook local).
No requiere Azure AD ni credenciales de API.
Requiere que Outlook esté instalado y con sesión activa en el equipo.

Variables de entorno (.env):
  OUTLOOK_RECIPIENT_EMAIL — uno o varios destinatarios separados por coma
  OUTLOOK_SUBJECT_PREFIX  — prefijo del asunto (ej. [SAP])
  OUTLOOK_SYSTEM_NAME     — nombre del sistema en el correo
  OUTLOOK_COMPANY_NAME    — nombre de la empresa en la cabecera
  OUTLOOK_SYSTEM_SUBTITLE — subtítulo de la cabecera
  OUTLOOK_FOOTER_TEXT     — texto del pie de página
"""
from __future__ import annotations

import os
import re as _re
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class OutlookNotifier:
    """Cliente de notificaciones por correo usando Outlook local (win32com)."""

    def __init__(self) -> None:
        def _parse_email(s: str) -> str:
            m = _re.search(r"<([^>]+)>", s)
            return m.group(1).strip() if m else s.strip()

        raw = os.getenv("OUTLOOK_RECIPIENT_EMAIL", "")
        self.recipients: list[str] = [_parse_email(e) for e in raw.split(",") if e.strip()]

        self.subject_prefix  = os.getenv("OUTLOOK_SUBJECT_PREFIX",  "[SAP]")
        self.system_name     = os.getenv("OUTLOOK_SYSTEM_NAME",     "Robot SAP Comisiones")
        self.company_name    = os.getenv("OUTLOOK_COMPANY_NAME",    "ASIAUTO S.A.")
        self.system_subtitle = os.getenv("OUTLOOK_SYSTEM_SUBTITLE", "Automatización FI — Comisiones Bancarias")
        self.footer_text     = os.getenv("OUTLOOK_FOOTER_TEXT",     "Mensaje generado automáticamente — no responder.")

        self._enabled = bool(self.recipients)
        if not self._enabled:
            logger.info("OutlookNotifier deshabilitado (OUTLOOK_RECIPIENT_EMAIL no configurado).")
        else:
            logger.info("OutlookNotifier listo (Outlook win32com) → %s", ", ".join(self.recipients))

    # ── PLANTILLA HTML ────────────────────────────────────────────────────────

    def _html(self, titulo: str, color_titulo: str, cuerpo: str, batch_label: str) -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f4f6f8;font-family:Arial,sans-serif;font-size:14px;color:#333;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8;padding:24px 0;">
    <tr><td align="center">
    <table width="620" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:6px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08);">

      <tr>
        <td style="background:#111111;padding:20px 32px;">
          <span style="color:#ffffff;font-size:20px;font-weight:bold;letter-spacing:1px;">
            {self.company_name}
          </span>
          <span style="color:#cccccc;font-size:13px;margin-left:12px;">
            {self.system_subtitle}
          </span>
        </td>
      </tr>

      <tr>
        <td style="padding:24px 32px 8px 32px;border-bottom:3px solid {color_titulo};">
          <h2 style="margin:0;color:{color_titulo};font-size:18px;">{titulo}</h2>
        </td>
      </tr>

      <tr>
        <td style="padding:24px 32px;">
          {cuerpo}
        </td>
      </tr>

      <tr>
        <td style="background:#f4f6f8;padding:14px 32px;border-top:1px solid #e0e0e0;">
          <p style="margin:0;font-size:11px;color:#888;">
            Ejecución: <strong>{batch_label}</strong> &nbsp;|&nbsp; {timestamp}
            &nbsp;|&nbsp; {self.footer_text}
          </p>
        </td>
      </tr>

    </table>
    </td></tr>
  </table>
</body>
</html>"""

    # ── ENVÍO ─────────────────────────────────────────────────────────────────

    def _send_email(self, subject: str, html_body: str) -> None:
        if not self._enabled:
            logger.info("Email omitido (notificador no configurado): %s", subject)
            return
        try:
            import win32com.client
            outlook = win32com.client.Dispatch("Outlook.Application")
            mail    = outlook.CreateItem(0)
            mail.To      = "; ".join(self.recipients)
            mail.Subject = subject
            mail.HTMLBody = html_body
            mail.Send()
            logger.info("Correo enviado a [%s]: %s", ", ".join(self.recipients), subject)
        except Exception as exc:
            logger.warning("No se pudo enviar el correo '%s': %s", subject, exc)

    # ── NOTIFICACIÓN ERROR CRÍTICO ────────────────────────────────────────────

    def notify_critical(self, error_msg: str, batch_label: str,
                        description: str | None = None) -> None:
        texto = description or (
            f"El {self.system_name} fue interrumpido por un error inesperado. "
            "Se requiere revisión manual."
        )
        cuerpo = f"""
        <p style="margin:0 0 16px 0;color:#555;">{texto}</p>
        <pre style="margin:0;background:#fef6f6;color:#c0392b;padding:16px;
                    border:1px solid #f5c6cb;border-radius:4px;font-size:13px;
                    white-space:pre-wrap;word-break:break-word;">{error_msg}</pre>
        """
        self._send_email(
            subject=f"{self.subject_prefix} ERROR CRITICO — {self.system_name} {batch_label}",
            html_body=self._html(
                titulo=f"Error critico — {self.system_name} interrumpido",
                color_titulo="#c0392b",
                cuerpo=cuerpo,
                batch_label=batch_label,
            ),
        )
