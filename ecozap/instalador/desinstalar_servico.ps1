# EcoZap - remove a execução em segundo plano (rode como Administrador)
$nome = "EcoZap"
if (Get-ScheduledTask -TaskName $nome -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $nome -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $nome -Confirm:$false
    Write-Host "Serviço EcoZap removido."
}
