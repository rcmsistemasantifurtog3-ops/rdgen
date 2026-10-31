# EcoZap - instala as impressoras virtuais no Windows
# Rode no PowerShell COMO ADMINISTRADOR:
#   powershell -ExecutionPolicy Bypass -File .\instalar_impressora.ps1
#
# Truque usado: uma "Porta Local" cujo NOME é um caminho de arquivo faz o
# spooler gravar a impressão direto nesse arquivo, sem perguntar "Salvar como".
# Nenhum driver próprio é instalado: só drivers que já vêm no Windows.

param(
    [string]$Pasta = "C:\ProgramData\EcoZap\entrada"
)

$ErrorActionPreference = "Stop"

function Garantir-Porta([string]$Nome) {
    if (Get-PrinterPort -Name $Nome -ErrorAction SilentlyContinue) { return }
    try {
        Add-PrinterPort -Name $Nome
    } catch {
        # Plano B: registra a porta local direto no registro e reinicia o spooler
        $chave = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Ports"
        New-ItemProperty -Path $chave -Name $Nome -Value "" -PropertyType String -Force | Out-Null
        Restart-Service Spooler -Force
        Start-Sleep -Seconds 3
    }
}

function Garantir-Impressora([string]$Nome, [string]$Driver, [string]$Porta) {
    if (-not (Get-PrinterDriver -Name $Driver -ErrorAction SilentlyContinue)) {
        Add-PrinterDriver -Name $Driver
    }
    Garantir-Porta $Porta
    $existente = Get-Printer -Name $Nome -ErrorAction SilentlyContinue
    if ($existente) {
        Set-Printer -Name $Nome -PortName $Porta -DriverName $Driver
    } else {
        Add-Printer -Name $Nome -DriverName $Driver -PortName $Porta
    }
    Write-Host "  [ok] $Nome  ->  $Porta" -ForegroundColor Green
}

Write-Host "EcoZap - instalando impressoras virtuais" -ForegroundColor Cyan

# 1) Pasta de entrada, com permissão para o spooler gravar e o EcoZap ler/mover
New-Item -ItemType Directory -Force -Path $Pasta | Out-Null
icacls $Pasta /grant "*S-1-5-32-545:(OI)(CI)M" /T | Out-Null   # Usuarios: modificar
icacls $Pasta /grant "*S-1-5-18:(OI)(CI)F" /T | Out-Null        # SYSTEM: total

# 2) Impressora "PDF": para sistemas que imprimem em A4 / layout gráfico (DANFE, NFC-e em PDF)
Garantir-Impressora "EcoZap PDF (WhatsApp)" "Microsoft Print To PDF" (Join-Path $Pasta "ecozap.pdf")

# 3) Impressora "Cupom": para PDV que imprime texto/ESC-POS em bobina (58/80 mm)
Garantir-Impressora "EcoZap Cupom (WhatsApp)" "Generic / Text Only" (Join-Path $Pasta "ecozap.txt")

Write-Host ""
Write-Host "Pronto! No seu sistema, escolha a impressora 'EcoZap PDF' ou 'EcoZap Cupom'." -ForegroundColor Cyan
Write-Host "Deixe o EcoZap aberto (iniciar_ecozap.bat) para receber as impressões."
