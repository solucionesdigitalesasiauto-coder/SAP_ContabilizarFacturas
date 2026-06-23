# -*- coding: utf-8 -*-
"""Diagnostico: que ventana tiene el foco cuando presionamos Tab."""
import sys, time, win32api, win32con, win32gui, pyautogui
sys.stdout.reconfigure(encoding='utf-8')
import sap_gui as SAP

print(f"Pantalla: {SAP.titulo_actual()!r}")
print(f"hwnd inicial: {SAP._encontrar_hwnd():#010x}")

# SC_CLOSE
SAP.activar()
time.sleep(0.4)
hwnd = SAP._encontrar_hwnd()
win32api.PostMessage(hwnd, win32con.WM_SYSCOMMAND, win32con.SC_CLOSE, 0)
time.sleep(2)

# Listar TODAS las ventanas SAP_FRONTEND_SESSION
print("\n-- Todas las ventanas SAP_FRONTEND_SESSION --")
sapwins = []
def cb(h, _):
    if win32gui.GetClassName(h) == "SAP_FRONTEND_SESSION":
        t = win32gui.GetWindowText(h)
        sapwins.append((h, t))
        print(f"  {h:#010x}  titulo={t!r}")
win32gui.EnumWindows(cb, None)

# Ver qué tiene el foco ahora
fg = win32gui.GetForegroundWindow()
print(f"\nForeground actual: {fg:#010x}  class={win32gui.GetClassName(fg)!r}  titulo={win32gui.GetWindowText(fg)!r}")

# Activar hwnd encontrado por _encontrar_hwnd
hwnd2 = SAP._encontrar_hwnd()
print(f"\nActivando hwnd2={hwnd2:#010x}")
SAP.activar()
time.sleep(0.5)
fg2 = win32gui.GetForegroundWindow()
print(f"Foreground tras activar: {fg2:#010x}  class={win32gui.GetClassName(fg2)!r}  titulo={win32gui.GetWindowText(fg2)!r}")

print("\nProbando pyautogui.press('tab') + 'return'...")
pyautogui.press('tab')
time.sleep(0.3)
pyautogui.press('return')
time.sleep(2)

if SAP._encontrar_hwnd():
    print("SAP sigue abierto.")
else:
    print("SAP cerrado.")
