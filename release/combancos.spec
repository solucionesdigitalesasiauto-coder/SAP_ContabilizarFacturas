# -*- mode: python ; coding: utf-8 -*-
import glob as _glob
import os as _os

_TESS = r'C:\Program Files\Tesseract-OCR'
_tess_bins = [
    (f, '.') for f in _glob.glob(_os.path.join(_TESS, '*.dll'))
] + [(f, '.') for f in _glob.glob(_os.path.join(_TESS, 'tesseract.exe'))]
_tess_data = [
    (f, 'tessdata') for f in _glob.glob(_os.path.join(_TESS, 'tessdata', '*.traineddata'))
]

a = Analysis(
    ['../main.py'],
    pathex=['..', '../diagnostico'],
    binaries=_tess_bins,
    datas=_tess_data + [
        ('../bancos.json',                   '.'),
        ('../sap_gui.py',                    '.'),
        ('../transactions/__init__.py',      'transactions'),
        ('../transactions/zfiec015_kb.py',   'transactions'),
        ('../transactions/fb60_kb.py',       'transactions'),
        ('../transactions/validacion_Pantalla.py', 'transactions'),
        ('../correos/__init__.py',           'correos'),
        ('../correos/notificador_sap.py',    'correos'),
        ('../correos/outlook_notifier.py',   'correos'),
        ('../diagnostico/campos.py',         'diagnostico'),
    ],
    hiddenimports=[
        'win32com.client',
        'win32com.shell',
        'win32com.shell.shell',
        'win32api',
        'win32gui',
        'win32con',
        'win32process',
        'pynput.keyboard._win32',
        'pynput.mouse._win32',
        'pyautogui',
        'pyperclip',
        'dotenv',
        'msal',
        'requests',
        'pywinauto',
        'pywinauto.application',
        'pywinauto.controls.uia_controls',
        'pywinauto.controls.hwnd_wrapper',
        'pywinauto.base_wrapper',
        'pywinauto.uia_defines',
        'pywinauto.uia_element_info',
        'PIL',
        'PIL.ImageGrab',
        'PIL.ImageOps',
        'PIL.ImageEnhance',
        'pytesseract',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['imagenes'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ComBancos',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    onefile=True,
)
