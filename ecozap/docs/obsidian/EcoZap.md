---
projeto: EcoZap
tags: [projeto, ecozap, whatsapp, impressora-virtual, python]
status: v0.3 - pronto para teste
criado: 2026-10-31
atualizado: 2026-10-31
repo: rcmsistemasantifurtog3-ops/rdgen (pasta ecozap/)
branch: claude/eco-printer-whatsapp-kdhb9o
---

# EcoZap — impressora virtual que envia pelo WhatsApp

## Ideia
Simular uma impressora: o sistema da loja "imprime" recibo/cupom/NFC-e, o EcoZap
gera PDF/JPEG/PNG e envia pelo WhatsApp do cliente. Menos papel, cópia digital para reenviar.

## Versões
- **v0.1** — impressora virtual, conversão, envio, histórico, limpeza de 15 dias.
- **v0.2** — bandeja, agenda de clientes (CPF com HMAC), link/QR da NFC-e,
  painel web multi-caixa, modo serviço, relatório mensal, instalador .exe, CI.
- **v0.3** — cliente escreve primeiro (webhook Cloud/Evolution), cartaz do balcão,
  código + QR na tela do caixa, respostas automáticas, 2ª via, entregue/lido,
  promoções com opt-in (PROMO/SAIR), limites e agendamento.

## Decisões
- **Python 3.12** + fpdf2, pypdfium2, Pillow, requests, qrcode, zxing-cpp, pystray. Telas em Tkinter.
- **Captura sem driver próprio:** Porta Local do Windows com nome = caminho de arquivo.
  - *EcoZap Cupom* → "Generic / Text Only" (texto / ESC-POS)
  - *EcoZap PDF* → "Microsoft Print To PDF"
- **Fim da impressão:** tamanho estável + `os.replace` (falha enquanto o spooler escreve).
- **QR da NFC-e:** comando ESC/POS `GS ( k` (fn 80 guarda, fn 81 imprime) vira a marca
  `[[QR:...]]` no texto → redesenhado no PDF com `qrcode` → vira link na mensagem.
- **Telefone:** cupom (Tel:/Cel:/WhatsApp:) → agenda (por CPF) → janela com autocompletar.
- **CPF:** nunca em texto; HMAC-SHA256 com chave em `dados/chave_cpf.bin`.
- **Estatísticas** em tabela própria (`estatisticas_diarias`), não somem na limpeza.
  Reenvio não conta papel de novo.
- **Painel web:** só biblioteca padrão (`http.server`); Basic Auth; bloqueio de CSRF por Origin;
  na rede exige senha.
- **Serviço:** Agendador de Tarefas como SYSTEM (sem NSSM). Trava de instância única.
- **Instalador:** PyInstaller (EcoZap.exe + ecozap-cli.exe) + Inno Setup; GitHub Actions gera e testa.
- **WhatsApp:** `simulado` | `cloud_api` (oficial; template fora da janela de 24h) | `evolution`.

## Fluxo "cliente escreve primeiro" (v0.3)
- Abre a janela de 24h da Meta → comprovante vai **sem template e sem custo**.
- Mensagem → (1) código do comprovante, (2) único pendente nos últimos 10 min,
  (3) caixa escolhe na lista, (4) "2ª via", (5) boas-vindas + convite PROMO.
- Webhooks: `/webhook/whatsapp` (assinatura `X-Hub-Signature-256` com app_secret),
  `/webhook/evolution?token=` — fora da senha do painel, com segredo próprio.
- Meta precisa de HTTPS público → Cloudflare Tunnel.
- Celular antigo sem 9 (12 dígitos) é padronizado com 9.

## Promoções
- Só opt-in; rodapé SAIR fixo; 1 a cada 7 dias; horário 9–20h; pausa 8–20 s; 200/dia.
- Cloud API fora da janela: template MARKETING (`template_promocao`), senão "pulado".
- Consentimento registrado (data + origem) = prova para LGPD.

## Como testar
1. GitHub → Actions → "EcoZap - testes e instalador" → Artifacts → **EcoZap-Instalador**.
2. Instalar (admin) → Configurações → "Enviar teste..." (provedor simulado primeiro).
3. Bloco de Notas → imprimir na **EcoZap Cupom** com `Cel: (11) 98765-4321`.
4. Testar também um PDF/DANFE na **EcoZap PDF**.

## Pendências
- [ ] Testar instalador + impressoras num Windows real
- [ ] Testar com o PDV real (ESC/POS) e conferir acentos / QR
- [ ] Criar e aprovar template na Meta (cabeçalho Documento + {{1}} loja + {{2}} link)
- [ ] Certificado de assinatura de código (SmartScreen)
- [ ] Configurar webhook (Evolution na rede ou Cloudflare Tunnel para a Meta)
- [ ] Imprimir o cartaz do balcão e testar o fluxo com um celular
- [ ] Aprovar template MARKETING para promoções (Cloud API)
- [ ] Atualização automática / webhook de entregue-lido
