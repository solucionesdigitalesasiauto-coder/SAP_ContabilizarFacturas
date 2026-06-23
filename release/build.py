"""
build.py — Compila ComBancos y genera el ZIP de entrega.
Uso: python release/build.py   o doble clic en release/build_release.bat
"""
import os
import sys
import shutil
import subprocess
import zipfile
from datetime import date

BASE         = os.path.dirname(os.path.abspath(__file__))  # .../SAP/release/
PROJECT_ROOT = os.path.dirname(BASE)                        # .../SAP/


def _paso(n, total, texto):
    print(f"\n  [{n}/{total}] {texto}...")


def _ok(texto="OK"):
    print(f"        {texto}")


def _error(texto):
    print(f"\n  !! ERROR: {texto}")
    input("\n  Presiona Enter para cerrar...")
    sys.exit(1)


def main():
    # ── Versión ───────────────────────────────────────────────
    version_file = os.path.join(PROJECT_ROOT, "VERSION")
    version = open(version_file).read().strip() if os.path.exists(version_file) else "0.0.0"
    hoy     = date.today().strftime("%Y%m%d")
    nombre  = f"ComBancos_v{version}_{hoy}"

    # ── Rutas fuera de OneDrive para evitar bloqueos de sync ─
    release_root = r"C:\Temp\built_temp"
    release_dir  = os.path.join(release_root, nombre)
    zip_path     = os.path.join(release_root, f"{nombre}.zip")
    spec_path    = os.path.join(BASE, "combancos.spec")
    work_path    = r"C:\Temp\ComBancos_build"
    dist_path    = r"C:\Temp\ComBancos_dist"
    exe_path     = os.path.join(dist_path, "ComBancos.exe")

    print("=" * 56)
    print(f"  BUILD ComBancos v{version} — ASIAUTO S.A.")
    print("=" * 56)
    print(f"\n  Salida : {zip_path}")

    # ── Limpiar release anterior ──────────────────────────────
    os.makedirs(release_root, exist_ok=True)
    if os.path.exists(release_dir):
        shutil.rmtree(release_dir)
    if os.path.exists(zip_path):
        os.remove(zip_path)

    # ── 1. Compilar ───────────────────────────────────────────
    _paso(1, 3, "Compilando con PyInstaller")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", spec_path,
         "--clean",
         "--workpath", work_path,
         "--distpath", dist_path],
        cwd=PROJECT_ROOT
    )
    if result.returncode != 0:
        _error("PyInstaller falló. Revisa el log de arriba.")
    if not os.path.exists(exe_path):
        _error("ComBancos.exe no fue generado.")
    _ok(f"ComBancos.exe  ({os.path.getsize(exe_path) // 1024 // 1024} MB)")

    # ── 2. Armar carpeta de entrega ───────────────────────────
    _paso(2, 3, "Armando carpeta de entrega")
    os.makedirs(release_dir)

    # Ejecutable
    shutil.copy2(exe_path, release_dir)

    # Configuración
    shutil.copy2(os.path.join(PROJECT_ROOT, "bancos.json"), release_dir)
    shutil.copy2(os.path.join(PROJECT_ROOT, "VERSION"),     release_dir)

    # .env — credenciales reales
    env_src = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_src):
        shutil.copy2(env_src, release_dir)
        _ok(".env copiado")
    else:
        _error(".env no encontrado en la raíz del proyecto — agrega las credenciales SAP.")

    _ok("Carpeta de entrega lista")

    # ── 3. Crear ZIP ──────────────────────────────────────────
    _paso(3, 3, "Creando ZIP")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(release_dir):
            for f in files:
                full = os.path.join(root, f)
                arc  = os.path.relpath(full, release_dir)
                zf.write(full, arc)
    _ok(f"{os.path.getsize(zip_path) // 1024 // 1024} MB")

    print("\n" + "=" * 56)
    print(f"  LISTO: {nombre}.zip")
    print("=" * 56)
    os.startfile(release_root)


if __name__ == "__main__":
    main()
