---
projeto: EcoZap
tags: [projeto, ecozap, whatsapp, impressora-virtual, python]
status: mvp
criado: 2026-10-31
repo: rcmsistemasantifurtog3-ops/rdgen (pasta ecozap/)
branch: claude/eco-printer-whatsapp-kdhb9o
---

# EcoZap — impressora virtual que envia pelo WhatsApp

## Ideia
Simular uma impressora: o sistema da loja "imprime" o recibo/cupom/NFC-e, o EcoZap
gera PDF/JPEG/PNG e envia pelo WhatsApp do cliente. Objetivo: reduzir papel (que
quase todo mundo joga fora) e ainda ter cópia digital para reenviar.

## Decisões
- **Linguagem:** Python 3.10+ (fpdf2, pypdfium2, Pillow, requests) + Tkinter para telas.
- **Captura sem driver próprio:** Porta Local do Windows com nome = caminho de arquivo
  (mesmo truque do `printer-adapter` do repo). Duas impressoras:
  - *EcoZap Cupom* → driver "Generic / Text Only" (texto / ESC-POS de PDV)
  - *EcoZap PDF* → driver "Microsoft Print To PDF" (layout gráfico, A4, DANFE)
- **Fim da impressão detectado** por: tamanho estável + `os.replace` (falha no Windows
  enquanto o spooler está escrevendo).
- **Telefone:** extraído do cupom (Tel:, Cel:, WhatsApp:, ou celular formatado);
  não confunde com CPF. Senão, a janela pergunta.
- **WhatsApp:** provedores `simulado` (teste), `cloud_api` (oficial, recomendado,
  exige template fora da janela de 24h) e `evolution` (não oficial, risco de bloqueio).
- **Retenção:** 15 dias (config `dias_retencao`), limpeza ao abrir e a cada 6h.
  Guarda fiscal oficial é responsabilidade do emissor (XML, ~5 anos). LGPD: reter pouco.
- **Métrica eco:** mm de papel de cada comprovante → "metros economizados".

## Como rodar
- `pip install -r requirements.txt` → `copy config.exemplo.json config.json`
- `instalador/instalar_impressora.ps1` (admin) → `instalador/iniciar_ecozap.bat`
- Testes: `python -m pytest` (22 testes)

## Pendências / ideias
- [ ] Testar o instalador num Windows real (Add-PrinterPort com caminho de arquivo)
- [ ] Criar e aprovar template na Meta (cabeçalho Documento + {{1}} no corpo)
- [ ] Ícone na bandeja (pystray)
- [ ] Autocompletar cliente pelo CPF/nome
- [ ] Link do QR Code da NFC-e na mensagem
- [ ] Empacotar com PyInstaller + Inno Setup
