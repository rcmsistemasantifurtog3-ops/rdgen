# EcoZap - roda em segundo plano (sem janela) ao ligar o computador.
# Usa o Agendador de Tarefas do Windows como SYSTEM: não precisa de programa
# extra (NSSM) e reinicia sozinho se cair.
# Rode como Administrador:
#   powershell -ExecutionPolicy Bypass -File .\instalar_servico.ps1 -Exe "C:\Program Files\EcoZap\ecozap-cli.exe"
# Sem o .exe (rodando pelo Python):
#   ... -Exe "C:\caminho\.venv\Scripts\python.exe" -Argumentos "-m ecozap servico" -Pasta "C:\caminho\ecozap"
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [string]$Argumentos = "servico",
    [string]$Pasta = ""
)
$ErrorActionPreference = "Stop"
$nome = "EcoZap"
if (-not $Pasta) { $Pasta = Split-Path $Exe }

$acao = New-ScheduledTaskAction -Execute $Exe -Argument $Argumentos -WorkingDirectory $Pasta
$gatilho = New-ScheduledTaskTrigger -AtStartup
$usuario = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable

Unregister-ScheduledTask -TaskName $nome -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho -Principal $usuario `
    -Settings $ajustes -Description "EcoZap: impressora virtual que envia comprovantes pelo WhatsApp" | Out-Null
Start-ScheduledTask -TaskName $nome
Write-Host "Serviço EcoZap instalado e iniciado." -ForegroundColor Green
Write-Host "Ative o painel web no config.json (painel.ativo = true) para enviar os comprovantes sem telefone."
