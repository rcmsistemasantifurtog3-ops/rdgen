# 🌱 EcoZap — a impressora que imprime no WhatsApp

O EcoZap é uma **impressora virtual**. Quando o sistema da loja manda imprimir um
recibo, cupom, NFC-e ou DANFE, você escolhe a impressora **"EcoZap"** (com formato
e tamanho de folha). Em vez de gastar papel, o EcoZap:

1. recebe a impressão;
2. converte em **PDF**, **JPEG** ou **PNG** (o QR Code da NFC-e é redesenhado);
3. descobre o **WhatsApp do cliente**: no próprio cupom, pela **agenda** (CPF já
   conhecido) ou perguntando numa janelinha com **autocompletar**;
4. **envia pelo WhatsApp**, com o **link de consulta da nota fiscal** na mensagem;
5. **guarda por 15 dias** (configurável) para reenviar fácil, e depois apaga sozinho;
6. conta os **metros de papel economizados** e gera um **relatório mensal** + imagem
   pronta para as redes sociais. 🌳

```
 Sistema da loja ──imprimir──► "EcoZap Cupom/PDF"  (impressora do Windows)
                                     │  porta local = arquivo
                                     ▼
                     C:\ProgramData\EcoZap\entrada\ecozap.txt|.pdf
                                     │  EcoZap vigia a pasta
                                     ▼
     converte (ESC/POS → texto+QR → PDF → JPEG/PNG) ─► guarda 15 dias
     acha telefone (cupom / agenda pelo CPF) e link da NFC-e
                                     │
                                     ▼
          janela "WhatsApp do cliente" ou painel web  ──►  WhatsApp 📲
```

## Instalação rápida (recomendado): instalador `.exe`

O GitHub gera o instalador sozinho a cada alteração:

1. No repositório, abra **Actions → "EcoZap - testes e instalador"** → última execução verde.
2. Em **Artifacts**, baixe **EcoZap-Instalador** e descompacte.
3. Rode o `EcoZap-Instalador-x.y.z.exe` (pede permissão de administrador). Opções:
   - **Instalar as impressoras virtuais** ✔
   - **Abrir junto com o Windows** (ícone perto do relógio) — *recomendado*; **ou**
   - **Rodar em segundo plano, sem janela** (envio automático + painel web).
4. Abra o EcoZap → **Ferramentas → Configurações**: nome da loja, mensagem,
   provedor de WhatsApp. Use **"Enviar teste..."** para conferir.

> O Windows SmartScreen pode avisar "editor desconhecido" porque o instalador não
> tem certificado de assinatura de código. Clique em *Mais informações → Executar assim mesmo*.
> Para sumir com o aviso, compre um certificado de assinatura de código (veja "Próximos passos").

Gerar o instalador no seu PC: instale o Python e o [Inno Setup 6](https://jrsoftware.org/isdl.php)
e rode `instalador\build.bat`.

## Instalação pelo código-fonte (desenvolvimento)

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy config.exemplo.json config.json
powershell -ExecutionPolicy Bypass -File .\instalador\instalar_impressora.ps1   (como administrador)
.venv\Scripts\python -m ecozap
```

## Duas impressoras, para cobrir todos os sistemas

| Impressora | Driver (já vem no Windows) | Quando usar |
|---|---|---|
| **EcoZap Cupom (WhatsApp)** | Generic / Text Only | PDV que imprime em bobina térmica (texto ou ESC/POS cru). O EcoZap remonta o cupom em 58 mm, 80 mm ou A4 e redesenha o QR Code da NFC-e. |
| **EcoZap PDF (WhatsApp)** | Microsoft Print To PDF | Sistemas que imprimem layout gráfico (DANFE, NFC-e, recibos A4). O tamanho de folha escolhido na impressão é mantido. |

Teste: abra o Bloco de Notas, digite `TOTAL 10,00  Cel: (11) 98765-4321` e imprima
na **EcoZap Cupom**. A janela de envio aparece com o número já preenchido.

## Recursos

### Ícone na bandeja (perto do relógio)
Fechar a janela **não fecha** o EcoZap: ele continua recebendo. Clique com o botão
direito no ícone 🍃 para: abrir, **pausar o recebimento** (as impressões esperam na
fila e são processadas ao retomar; o ícone fica cinza), relatório do mês, painel
web, configurações e sair.

### Agenda de clientes (autocompletar)
- Cada envio grava **telefone + nome** na agenda.
- Se o cupom trouxer **CPF**, o EcoZap guarda só um **resumo irreversível** (HMAC-SHA256
  com chave secreta da instalação), nunca o CPF em texto. Na próxima compra com o mesmo
  CPF, telefone e nome **aparecem sozinhos**.
- Na janela de envio, comece a digitar nome ou telefone: aparecem sugestões.
- **Ferramentas → Agenda de clientes**: busca e botão **"Esquecer cliente"** (LGPD).
- A agenda é limpa depois de `dias_retencao_clientes` (padrão 365) sem compras.

### NFC-e / NF-e: link de consulta na mensagem
O EcoZap procura, nesta ordem: o **QR Code** enviado pelo PDV (ESC/POS), o QR Code
desenhado no PDF e o endereço de consulta impresso. Também valida a **chave de acesso**
(44 dígitos, dígito verificador). A mensagem ganha:
> 🧾 Consulte sua nota fiscal: https://www.nfce.fazenda.sp.gov.br/qrcode?p=...

Sem link, vai a chave de acesso. Textos editáveis em `mensagem_nfce` e `mensagem_chave`.

### Painel web (vários caixas e lojas)
Ative em **Configurações → Painel web** (ou `painel.ativo`). Abra `http://127.0.0.1:8765`.
- Lista, busca, envia/reenvia, abre o PDF, descarta;
- Relatório do mês e imagem para redes;
- **Visão geral**: soma os números de outros caixas listados em `painel.caixas`:
  ```json
  "caixas": [{"nome": "Caixa 2", "url": "http://192.168.0.12:8765", "senha": "..."}]
  ```
- Para acessar de outro PC/celular da rede: `host` = `0.0.0.0` **e** defina uma `senha`
  (o EcoZap se recusa a abrir na rede sem senha). Use só dentro da rede da loja.
- API JSON: `GET /api/estatisticas`, `GET /api/comprovantes?busca=`, `POST /api/enviar`
  (`{"id": "...", "telefone": "...", "formato": "pdf"}`).

### Modo serviço (sem janela)
Opção do instalador, ou `instalador\instalar_servico.ps1`. Roda como SYSTEM pelo
Agendador de Tarefas ao ligar o PC e reinicia sozinho se cair. Envia automaticamente
quando acha o telefone (no cupom ou na agenda); os outros ficam **pendentes no painel web**.
Interface e serviço **não rodam juntos** (o EcoZap impede, para não disputarem as impressões).

### Relatório mensal "papel economizado"
**Ferramentas → Relatório do mês**, bandeja, painel web ou `ecozap relatorio 2026-10`.
Gera um **PDF** (números do mês, gráfico por dia, equivalência em bobinas e peso) e
uma **imagem 1080×1080** para Instagram/Status. Os números ficam guardados para sempre
(só contagens, sem dados de clientes), mesmo depois da limpeza de 15 dias.

## Configuração (`config.json`)

Fica em `C:\ProgramData\EcoZap\config.json` (instalador) ou na pasta atual. Quase tudo
pode ser editado pela tela **Configurações**.

| Campo | O que faz |
|---|---|
| `pasta_entrada` / `pasta_dados` | Onde a impressora grava / onde ficam histórico, agenda, relatórios e `ecozap.log`. |
| `dias_retencao` | Dias para guardar comprovantes (padrão **15**). |
| `dias_retencao_clientes` | Dias sem compra até apagar o cliente da agenda (0 = nunca). |
| `formato_padrao` / `tamanho_papel_padrao` | `pdf`/`jpeg`/`png` e `58mm`/`80mm`/`a4`. |
| `codificacao_texto` | `auto` (UTF-8, CP850, CP1252) ou fixa uma. |
| `nome_loja`, `identificacao_caixa` | Aparecem na mensagem, relatório e painel. |
| `mensagem`, `mensagem_nfce`, `mensagem_chave` | Textos. Variáveis: `{nome}`, `{nome_completo}`, `{loja}`, `{link}`, `{chave}`, `{caixa}`. |
| `usar_bandeja` | Ícone perto do relógio. |
| `whatsapp.provedor` | `simulado`, `cloud_api` ou `evolution`. |
| `painel.*` | `ativo`, `host`, `porta`, `usuario`, `senha`, `caixas`. |

## Provedores de WhatsApp

- **`simulado`** (padrão): não envia nada, salva em `dados\simulado\`. Para testar.
- **`cloud_api`** — **recomendado.** API oficial da Meta (WhatsApp Business Platform).
  - Precisa de conta no *Meta for Developers*, número dedicado, `token` permanente e `phone_number_id`.
  - ⚠️ **Regra da Meta:** para falar com quem **não** mandou mensagem à loja nas últimas
    24 h é obrigatório **template aprovado**. Crie um template de categoria *Utilidade*,
    **cabeçalho Documento** e corpo com variáveis, por exemplo:
    *"Olá! Segue o comprovante da sua compra na {{1}}. Consulte a nota: {{2}}"*.
    Configure `template` com o nome e `parametros_template` com `["{loja}", "{link}"]`.
  - Cada conversa iniciada pela empresa é cobrada pela Meta.
- **`evolution`** — [Evolution API](https://doc.evolution-api.com) (self-hosted, via QR Code
  num WhatsApp comum). Sem custo por mensagem, **mas não é oficial**: risco de bloqueio do número.

## Linha de comando

`ecozap-cli.exe` (instalado) ou `python -m ecozap`:

```bat
ecozap                          :: interface
ecozap --escondido              :: só o ícone na bandeja
ecozap servico                  :: sem tela (envio automático + painel web)
ecozap enviar recibo.pdf 11987654321 --formato jpeg
ecozap historico 98765          :: busca
ecozap reenviar <ID> [novo telefone]
ecozap relatorio 2026-10        :: PDF + imagem do mês
ecozap clientes [busca]         :: agenda
ecozap esquecer 11987654321     :: LGPD: apaga cliente da agenda
ecozap limpar
```

## Retenção, nota fiscal e LGPD

- **Fiscal:** quem precisa guardar o documento fiscal (XML da NF-e/NFC-e, ~5 anos) é o
  **sistema emissor**. O EcoZap guarda só uma *cópia de conveniência*. Confirme com o contador.
- **LGPD:** telefone e nome são dados pessoais. O EcoZap guarda pouco tempo, apaga
  sozinho, não guarda CPF em texto e tem "esquecer cliente". Avise o cliente que o
  comprovante vai pelo WhatsApp e envie só com o consentimento dele.

## Desenvolvimento

```bash
pip install -r requirements.txt pytest
python -m pytest            # 46 testes; rodam em Linux e Windows, sem WhatsApp real
```

```
ecozap/
├── ecozap/
│   ├── __main__.py      linha de comando
│   ├── interface.py     telas (Tkinter): envio, histórico, configurações, agenda
│   ├── bandeja.py       ícone perto do relógio (pystray)
│   ├── painel.py        painel web + API (biblioteca padrão)
│   ├── servico.py       regra de negócio: receber, guardar, enviar, reenviar
│   ├── monitor.py       vigia a pasta da impressora virtual
│   ├── conversor.py     PDF / texto / imagem → PDF, JPEG, PNG (+ QR Code)
│   ├── escpos.py        remove comandos da térmica (e preserva o QR Code)
│   ├── nfce.py          link de consulta e chave de acesso da NFC-e / NF-e
│   ├── clientes.py      CPF, nome do cliente e resumo seguro do CPF
│   ├── telefone.py      normaliza e acha o telefone no cupom
│   ├── armazenamento.py SQLite: histórico, agenda, estatísticas, limpeza
│   ├── relatorio.py     relatório mensal (PDF) e imagem para redes
│   ├── whatsapp.py      provedores: simulado, Cloud API, Evolution
│   ├── icone.py         ícone desenhado em código
│   ├── instancia.py     impede dois EcoZap ao mesmo tempo
│   └── config.py
├── instalador/          impressoras, serviço, PyInstaller (.spec), Inno Setup (.iss), build.bat
├── tests/
└── docs/obsidian/       anotações do projeto para o seu cofre do Obsidian
```

O workflow `.github/workflows/ecozap.yml` roda os testes (Linux + Windows), gera os
`.exe`, **testa o executável** e monta o instalador.

## Próximos passos (ideias)

- **Assinatura de código** do instalador (certificado OV/EV) para acabar com o aviso do SmartScreen.
- **Atualização automática**: o EcoZap verificar a última versão no GitHub Releases.
- **Webhook da Cloud API** para marcar "entregue/lido" no histórico.
- **Pergunta no PDV**: integração direta com o sistema da loja (API) para mandar o telefone junto com o cupom.
- **Painel central na nuvem** juntando todas as lojas (hoje a visão geral é pela rede local).
- **Mensagem de pós-venda** opcional (pesquisa de satisfação), sempre com consentimento do cliente.
