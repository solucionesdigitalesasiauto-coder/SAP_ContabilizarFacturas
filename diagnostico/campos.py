"""
IDs de campos SAP GUI y nombres de columnas.
Calibrar con discover_fields.py cuando algún campo falle.
"""

# ─────────────────────────────────────────────────────────────
# ZFIEC015 – Parámetros de entrada
# ─────────────────────────────────────────────────────────────

ZFIEC_SOCIEDAD       = "wnd[0]/usr/ctxtP_BUKRS"
ZFIEC_SOCIEDAD_ALT   = "wnd[0]/usr/ctxtS_BUKRS-LOW"

ZFIEC_PROVEEDOR      = "wnd[0]/usr/ctxtS_LIFNR-LOW"
ZFIEC_PROVEEDOR_ALT  = "wnd[0]/usr/ctxtP_LIFNR"

ZFIEC_FECHA_DESDE    = "wnd[0]/usr/ctxtS_FECHA-LOW"
ZFIEC_FECHA_DESDE_ALT= "wnd[0]/usr/ctxtP_DATDE"

ZFIEC_FECHA_HASTA    = "wnd[0]/usr/ctxtS_FECHA-HIGH"
ZFIEC_FECHA_HASTA_ALT= "wnd[0]/usr/ctxtP_DATHA"

ZFIEC_TIPO_DOC       = "wnd[0]/usr/ctxtP_TIPDC"
ZFIEC_TIPO_DOC_ALT   = "wnd[0]/usr/ctxtS_TIPDC-LOW"

ZFIEC_RADIO_PENDIENTE  = "wnd[0]/usr/radRB_PEND"
ZFIEC_RADIO_PEND_ALT1  = "wnd[0]/usr/radP_PEND"
ZFIEC_RADIO_PEND_ALT2  = "wnd[0]/usr/rad_PEND"

# ─────────────────────────────────────────────────────────────
# ZFIEC015 – Grilla de resultados (ALV Grid)
# ─────────────────────────────────────────────────────────────

ZFIEC_GRID           = "wnd[0]/usr/cntlGRID1/shellcontent"
ZFIEC_GRID_ALT1      = "wnd[0]/usr/cntlALV_GRID_1/shellcontent"
ZFIEC_GRID_ALT2      = "wnd[0]/usr/cntlCONTAINER1/shellcontent"

ZFIEC_COL_ESTADO     = "MSTAT"       # Columna de semáforo/error
ZFIEC_COL_FB60       = "FB60"        # Columna botón FB60
ZFIEC_COL_CLAVE      = "CLAVE_ACC"   # Columna Clave de Acceso (log)
ZFIEC_COL_NUMDOC     = "NUMDOC"      # Columna Nº documento (log)

# ─────────────────────────────────────────────────────────────
# FB60 – Cabecera (programa SAPMF05A, pantalla 0720)
# ─────────────────────────────────────────────────────────────

# Pantalla 1100 confirmada con Datos Técnicos (18/06/2026): INVFO-BUDAT / INVFO-BLDAT
_HDR_1100 = "wnd[0]/usr/subSCREEN_HEADER:SAPMF05A:0700/subSUBSCREEN_HEADER:SAPMF05A:1100"
FB60_FECHA_FACTURA    = f"{_HDR_1100}/ctxtINVFO-BLDAT"
FB60_FECHA_CONTAB     = f"{_HDR_1100}/ctxtINVFO-BUDAT"

# Resto de campos cabecera (pantalla 0720 — calibrar con discover_fields si fallan)
_HDR = "wnd[0]/usr/subSCREEN_HEADER:SAPMF05A:0700/subSUBSCREEN_HEADER:SAPMF05A:0720"
FB60_IMPORTE          = f"{_HDR}/txtRF05A-WRBTR"
FB60_CALC_IMPUESTOS   = f"{_HDR}/chkFUNCTION-001"
FB60_IND_IMPUESTO     = f"{_HDR}/ctxtRF05A-MWSKZ"

# ─────────────────────────────────────────────────────────────
# FB60 – Pestañas (TabStrip)
# ─────────────────────────────────────────────────────────────

FB60_TABSTRIP         = "wnd[0]/usr/tabsTAB_STRIP_HEADER"
FB60_TAB_DATOS        = f"{FB60_TABSTRIP}/tabpTABF01"
FB60_TAB_PAGO         = f"{FB60_TABSTRIP}/tabpTABF02"
FB60_TAB_DETALLE      = f"{FB60_TABSTRIP}/tabpTABF03"

# ─────────────────────────────────────────────────────────────
# FB60 – Pestaña Pago (pantalla 0820)
# ─────────────────────────────────────────────────────────────

_HDR_PAGO             = "wnd[0]/usr/subSCREEN_HEADER:SAPMF05A:0700/subSUBSCREEN_HEADER:SAPMF05A:0820"
FB60_VIA_PAGO         = f"{_HDR_PAGO}/ctxtRF05A-ZLSCH"

# ─────────────────────────────────────────────────────────────
# FB60 – Pestaña Detalle (pantalla 0750)
# ─────────────────────────────────────────────────────────────

_HDR_DET              = "wnd[0]/usr/subSCREEN_HEADER:SAPMF05A:0700/subSUBSCREEN_HEADER:SAPMF05A:0750"
FB60_TXT_CABEC        = f"{_HDR_DET}/txtRF05A-BKTXT"

# ─────────────────────────────────────────────────────────────
# FB60 – Tabla de posiciones (GuiTableControl)
# ─────────────────────────────────────────────────────────────

FB60_TABLA_POS        = "wnd[0]/usr/tblSAPMF05AACCIT"
FB60_POS_CUENTA_MAYOR = "ctxtACCIT-HKONT"
FB60_POS_IMPORTE      = "txtACCIT-WRBTR"
FB60_POS_IND_IMPUESTO = "ctxtACCIT-MWSKZ"
FB60_POS_TEXTO        = "txtACCIT-SGTXT"
FB60_POS_CENTRO_COSTO = "ctxtACCIT-KOSTL"

# ─────────────────────────────────────────────────────────────
# FB60 – Detalle de posición (cuando Centro de Costo no es visible)
# ─────────────────────────────────────────────────────────────

FB60_POS_DET_CENTRO   = "wnd[0]/usr/ctxtCOBL-KOSTL"
FB60_POS_DET_CENTRO_ALT = "wnd[1]/usr/ctxtCOBL-KOSTL"

# ─────────────────────────────────────────────────────────────
# FB60 – Popup de confirmación de contabilización
# ─────────────────────────────────────────────────────────────

FB60_POPUP_MSG1       = "usr/txtMESSTXT1"
FB60_POPUP_MSG2       = "usr/txtMESSAGE"
