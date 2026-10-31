# -*- mode: python ; coding: utf-8 -*-
# Gera dist/EcoZap/ com dois executáveis que compartilham as bibliotecas:
#   EcoZap.exe      -> interface (sem janela preta de console)
#   ecozap-cli.exe  -> linha de comando / modo serviço
# Uso:  pyinstaller --noconfirm --clean instalador/ecozap.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

raiz = Path(SPECPATH).parent
icone = str(raiz / "instalador" / "ecozap.ico")

datas, binaries = [], []
hidden = ["pystray._win32", "PIL._tkinter_finder"]
for pacote in ("pypdfium2", "pypdfium2_raw", "zxingcpp", "fpdf", "qrcode"):
    try:
        d, b, h = collect_all(pacote)
    except Exception:
        continue
    datas += d
    binaries += b
    hidden += h

a = Analysis(
    [str(raiz / "iniciar.py")],
    pathex=[str(raiz)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["pytest", "numpy"],
)
pyz = PYZ(a.pure)

gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="EcoZap",
          console=False, icon=icone, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ecozap-cli",
          console=True, icon=icone, upx=False)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="EcoZap", upx=False)
