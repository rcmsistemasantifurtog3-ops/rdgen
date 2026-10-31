@echo off
rem Abre o EcoZap sem janela de console. Para iniciar junto com o Windows,
rem coloque um atalho deste arquivo em: Win+R -> shell:startup
cd /d "%~dp0.."
if exist .venv\Scripts\pythonw.exe (
    start "" .venv\Scripts\pythonw.exe -m ecozap
) else (
    start "" pythonw -m ecozap
)
