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

# ── Rutas de build (fuera de OneDrive para evitar bloqueos de sync) ──
_BUILD_ROOT  = r"C:\Temp\built_temp"        # carpeta raíz de salida
_BUILD_WORK  = r"C:\Temp\ComBancos_build"   # directorio de trabajo PyInstaller
_BUILD_DIST  = r"C:\Temp\ComBancos_dist"    # directorio dist PyInstaller
_EXE_NAME    = "ComBancos.exe"              # nombre del ejecutable generado
_PREFIX_ZIP  = "ComBancos_v"               # prefijo del archivo ZIP de entrega

# ── Certificado de firma (compartido con gestor_amt) ─────────
_CERT_DIR  = r"C:\Users\wquintana\OneDrive - ASIAUTO S.A\Documentos\Matriculación\Telegram\gestor_amt"
_CERT_PFX  = os.path.join(_CERT_DIR, "asiauto_codesign.pfx")
_CERT_PWD  = os.path.join(_CERT_DIR, "codesign_password.txt")


def _encontrar_signtool() -> str | None:
    """Busca signtool.exe en las rutas típicas del Windows SDK."""
    import glob
    patrones = [
        r"C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe",
        r"C:\Program Files\Windows Kits\10\bin\*\x64\signtool.exe",
    ]
    for p in patrones:
        resultados = sorted(glob.glob(p), reverse=True)
        if resultados:
            return resultados[0]
    return None


def _paso(n, total, texto):
    """Imprime un encabezado numerado para un paso del proceso de build.

    Args:
        n (int): Número del paso actual.
        total (int): Total de pasos.
        texto (str): Descripción del paso.

    Returns:
        None
    """
    print(f"\n  [{n}/{total}] {texto}...")


def _ok(texto="OK"):
    """Imprime mensaje de confirmación de paso completado.

    Args:
        texto (str): Mensaje de confirmación. Default: "OK".

    Returns:
        None
    """
    print(f"        {texto}")


def _error(texto):
    """Imprime mensaje de error fatal, espera confirmación del usuario y sale.

    Args:
        texto (str): Descripción del error.

    Returns:
        None  (nunca retorna — llama sys.exit(1))
    """
    print(f"\n  !! ERROR: {texto}")
    input("\n  Presiona Enter para cerrar...")
    sys.exit(1)


def main():
    """Orquesta el build completo de ComBancos en 3 pasos:
    1. Compilar exe con PyInstaller usando combancos.spec.
    2. Armar carpeta de entrega (exe + bancos.json + VERSION + .env).
    3. Crear ZIP comprimido listo para distribuir.

    Returns:
        None

    Hardcoded:
        - _BUILD_ROOT = r"C:\\Temp\\built_temp"   (PATH — raíz de salida)
        - _BUILD_WORK = r"C:\\Temp\\ComBancos_build" (PATH — work PyInstaller)
        - _BUILD_DIST = r"C:\\Temp\\ComBancos_dist"  (PATH — dist PyInstaller)
        - _EXE_NAME = "ComBancos.exe"               (STRING — nombre del exe)
        - "bancos.json", "VERSION", ".env"           (STRING — archivos a incluir)
        - 3: total de pasos del build                (NÚMERO MÁGICO)
    """
    # ── Versión ───────────────────────────────────────────────
    version_file = os.path.join(PROJECT_ROOT, "VERSION")
    version = open(version_file).read().strip() if os.path.exists(version_file) else "0.0.0"
    hoy     = date.today().strftime("%Y%m%d")
    nombre  = f"{_PREFIX_ZIP}{version}_{hoy}"

    release_dir = os.path.join(_BUILD_ROOT, nombre)
    zip_path    = os.path.join(_BUILD_ROOT, f"{nombre}.zip")
    spec_path   = os.path.join(BASE, "combancos.spec")
    exe_path    = os.path.join(_BUILD_DIST, _EXE_NAME)

    print("=" * 56)
    print(f"  BUILD ComBancos v{version} — ASIAUTO S.A.")
    print("=" * 56)
    print(f"\n  Salida : {zip_path}")

    # ── Limpiar release anterior ──────────────────────────────
    os.makedirs(_BUILD_ROOT, exist_ok=True)
    if os.path.exists(release_dir):
        shutil.rmtree(release_dir)
    if os.path.exists(zip_path):
        os.remove(zip_path)

    # ── 1. Compilar ───────────────────────────────────────────
    _paso(1, 4, "Compilando con PyInstaller")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", spec_path,
         "--clean",
         "--workpath", _BUILD_WORK,
         "--distpath", _BUILD_DIST],
        cwd=PROJECT_ROOT
    )
    if result.returncode != 0:
        _error("PyInstaller falló. Revisa el log de arriba.")
    if not os.path.exists(exe_path):
        _error(f"{_EXE_NAME} no fue generado.")
    _ok(f"{_EXE_NAME}  ({os.path.getsize(exe_path) // 1024 // 1024} MB)")

    # ── 2. Firmar exe ─────────────────────────────────────────
    _paso(2, 4, "Firmando exe (SmartScreen)")
    signtool = _encontrar_signtool()
    if not signtool:
        print("        [!] signtool.exe no encontrado — exe sin firmar")
    elif not os.path.exists(_CERT_PFX):
        print(f"        [!] Certificado no encontrado: {_CERT_PFX}")
    else:
        pwd = open(_CERT_PWD).read().strip()
        r = subprocess.run(
            [signtool, "sign", "/f", _CERT_PFX, "/p", pwd,
             "/fd", "SHA256", "/t", "http://timestamp.digicert.com", exe_path],
            capture_output=True, text=True
        )
        if r.returncode == 0:
            _ok("Firmado correctamente")
        else:
            print(f"        [!] Error al firmar:\n{r.stdout}{r.stderr}")

    # ── 3. Armar carpeta de entrega ───────────────────────────
    _paso(3, 4, "Armando carpeta de entrega")
    os.makedirs(release_dir)

    shutil.copy2(exe_path, release_dir)
    shutil.copy2(os.path.join(PROJECT_ROOT, "bancos.json"), release_dir)
    shutil.copy2(os.path.join(PROJECT_ROOT, "VERSION"),     release_dir)

    env_src = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_src):
        shutil.copy2(env_src, release_dir)
        _ok(".env copiado")
    else:
        _error(".env no encontrado en la raíz del proyecto — agrega las credenciales SAP.")

    cer_src = os.path.join(_CERT_DIR, "asiauto_codesign.cer")
    if os.path.exists(cer_src):
        shutil.copy2(cer_src, release_dir)
        _ok("asiauto_codesign.cer incluido")
    else:
        print("        [!] Certificado .cer no encontrado — el cliente deberá instalarlo manualmente")

    _ok("Carpeta de entrega lista")

    # ── 4. Crear ZIP ──────────────────────────────────────────
    _paso(4, 4, "Creando ZIP")
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
    os.startfile(_BUILD_ROOT)


if __name__ == "__main__":
    main()
