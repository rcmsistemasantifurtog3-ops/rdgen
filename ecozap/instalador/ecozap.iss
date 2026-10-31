; EcoZap - instalador para Windows (Inno Setup 6)
; Compilar:  ISCC.exe /DAppVersion=0.2.0 instalador\ecozap.iss
; Antes, gere dist\EcoZap com o PyInstaller (veja build.bat).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{B3E1C0A4-5F2D-4C7B-9E1A-6D2F8A0C4E11}
AppName=EcoZap
AppVersion={#AppVersion}
AppVerName=EcoZap {#AppVersion}
AppPublisher=RCM Sistemas
DefaultDirName={autopf}\EcoZap
DefaultGroupName=EcoZap
OutputDir=..\dist
OutputBaseFilename=EcoZap-Instalador-{#AppVersion}
SetupIconFile=ecozap.ico
UninstallDisplayIcon={app}\EcoZap.exe
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "impressoras"; Description: "Instalar as impressoras virtuais EcoZap Cupom e EcoZap PDF"; GroupDescription: "Impressoras:"
Name: "iniciar"; Description: "Abrir o EcoZap junto com o Windows (ícone perto do relógio) - recomendado"; GroupDescription: "Como rodar:"; Flags: exclusive
Name: "servico"; Description: "Rodar em segundo plano, sem janela (envio automático + painel web)"; GroupDescription: "Como rodar:"; Flags: exclusive unchecked
Name: "atalho"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos:"

[Dirs]
Name: "{commonappdata}\EcoZap"; Permissions: users-modify
Name: "{commonappdata}\EcoZap\dados"; Permissions: users-modify
Name: "{commonappdata}\EcoZap\entrada"; Permissions: users-modify

[Files]
Source: "..\dist\EcoZap\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "instalar_impressora.ps1"; DestDir: "{app}\instalador"; Flags: ignoreversion
Source: "desinstalar_impressora.ps1"; DestDir: "{app}\instalador"; Flags: ignoreversion
Source: "instalar_servico.ps1"; DestDir: "{app}\instalador"; Flags: ignoreversion
Source: "desinstalar_servico.ps1"; DestDir: "{app}\instalador"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\config.exemplo.json"; DestDir: "{commonappdata}\EcoZap"; DestName: "config.json"; Flags: onlyifdoesntexist uninsneveruninstall

[Icons]
Name: "{group}\EcoZap"; Filename: "{app}\EcoZap.exe"
Name: "{group}\Pasta de dados do EcoZap"; Filename: "{commonappdata}\EcoZap"
Name: "{group}\Desinstalar EcoZap"; Filename: "{uninstallexe}"
Name: "{autodesktop}\EcoZap"; Filename: "{app}\EcoZap.exe"; Tasks: atalho
Name: "{commonstartup}\EcoZap"; Filename: "{app}\EcoZap.exe"; Parameters: "--escondido"; Tasks: iniciar

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\instalador\instalar_impressora.ps1"""; StatusMsg: "Instalando as impressoras virtuais..."; Flags: runhidden waituntilterminated; Tasks: impressoras
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\instalador\instalar_servico.ps1"" -Exe ""{app}\ecozap-cli.exe"""; StatusMsg: "Configurando o serviço..."; Flags: runhidden waituntilterminated; Tasks: servico
Filename: "{app}\EcoZap.exe"; Description: "Abrir o EcoZap agora"; Flags: postinstall nowait skipifsilent; Tasks: not servico

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/F /IM EcoZap.exe"; Flags: runhidden; RunOnceId: "fechar"
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\instalador\desinstalar_servico.ps1"""; Flags: runhidden waituntilterminated; RunOnceId: "servico"
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\instalador\desinstalar_impressora.ps1"""; Flags: runhidden waituntilterminated; RunOnceId: "impressoras"
