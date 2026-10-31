@echo off
rem Gera o instalador do EcoZap no seu PC (Windows).
rem Precisa: Python 3.10+ e Inno Setup 6 (https://jrsoftware.org/isdl.php)
rem O GitHub também gera automaticamente (aba Actions > EcoZap > Artifacts).
setlocal
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe python -m venv .venv
.venv\Scripts\pip install -r requirements.txt pyinstaller || exit /b 1
.venv\Scripts\python -c "from ecozap import icone; icone.salvar_ico('instalador/ecozap.ico')" || exit /b 1
.venv\Scripts\pyinstaller --noconfirm --clean --distpath dist --workpath build instalador\ecozap.spec || exit /b 1
for /f %%v in ('.venv\Scripts\python -c "import ecozap; print(ecozap.__version__)"') do set VERSAO=%%v
set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% (
    echo Inno Setup nao encontrado. O programa esta em dist\EcoZap, mas sem instalador.
    exit /b 1
)
%ISCC% /DAppVersion=%VERSAO% instalador\ecozap.iss || exit /b 1
echo.
echo Pronto: dist\EcoZap-Instalador-%VERSAO%.exe
