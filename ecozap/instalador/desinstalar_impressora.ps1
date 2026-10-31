# EcoZap - remove as impressoras virtuais (rode como Administrador)
param([string]$Pasta = "C:\ProgramData\EcoZap\entrada")

foreach ($nome in "EcoZap PDF (WhatsApp)", "EcoZap Cupom (WhatsApp)") {
    if (Get-Printer -Name $nome -ErrorAction SilentlyContinue) {
        Remove-Printer -Name $nome
        Write-Host "  [removida] $nome"
    }
}
foreach ($arq in "ecozap.pdf", "ecozap.txt") {
    $porta = Join-Path $Pasta $arq
    if (Get-PrinterPort -Name $porta -ErrorAction SilentlyContinue) {
        Remove-PrinterPort -Name $porta
    }
}
Write-Host "Impressoras removidas. Os comprovantes guardados NAO foram apagados."
