# 🌱 EcoZap — a impressora que imprime no WhatsApp

O EcoZap é uma **impressora virtual**. Quando o sistema da loja manda imprimir um
recibo, cupom, NFC-e ou DANFE, você escolhe a impressora **"EcoZap"** na tela de
impressão (com formato e tamanho de folha). Em vez de gastar papel, o EcoZap:

1. recebe a impressão;
2. converte em **PDF**, **JPEG** ou **PNG**;
3. abre uma janelinha pedindo o **WhatsApp do cliente** (ou acha o número sozinho no cupom);
4. **envia pelo WhatsApp**;
5. **guarda por 15 dias** (configurável) para reenviar fácil, e depois apaga sozinho.

E ainda mostra quantos **metros de papel** a loja deixou de gastar. 🌳

```
 Sistema da loja ──imprimir──► "EcoZap Cupom/PDF"  (impressora do Windows)
                                     │  porta local = arquivo
                                     ▼
                     C:\ProgramData\EcoZap\entrada\ecozap.txt|.pdf
                                     │  EcoZap vigia a pasta
                                     ▼
            converte (ESC/POS → texto → PDF → JPEG/PNG)  ──►  guarda 15 dias
                                     │
                                     ▼
                       janela "WhatsApp do cliente"  ──►  WhatsApp 📲
```

## Duas impressoras, para cobrir todos os sistemas

| Impressora | Driver (já vem no Windows) | Quando usar |
|---|---|---|
| **EcoZap Cupom (WhatsApp)** | Generic / Text Only | PDV que imprime em bobina térmica (texto ou ESC/POS cru). O EcoZap remonta o cupom em 58 mm, 80 mm ou A4. |
| **EcoZap PDF (WhatsApp)** | Microsoft Print To PDF | Sistemas que imprimem layout gráfico (DANFE, NFC-e com QR Code, recibos em A4). O tamanho de folha escolhido na impressão é mantido. |

O EcoZap identifica sozinho o que chegou (PDF, texto/ESC-POS ou imagem).

## Instalação (Windows)

1. Instale o **Python 3.10+** (marque *"Add Python to PATH"*).
2. Na pasta `ecozap`, abra o terminal e rode:
   ```bat
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   copy config.exemplo.json config.json
   ```
3. Edite o `config.json` (nome da loja, mensagem, provedor de WhatsApp).
4. Instale as impressoras (PowerShell **como administrador**):
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\instalador\instalar_impressora.ps1
   ```
5. Abra o EcoZap com `instalador\iniciar_ecozap.bat`.
   Para abrir junto com o Windows: `Win+R` → `shell:startup` → cole um atalho do `.bat`.

Teste: abra o Bloco de Notas, digite algo como `TOTAL 10,00  Cel: (11) 98765-4321`
e imprima na **EcoZap Cupom**. A janela de envio aparece com o número já preenchido.

## Configuração (`config.json`)

| Campo | O que faz |
|---|---|
| `pasta_entrada` | Pasta onde a impressora grava (a mesma do instalador). |
| `pasta_dados` | Onde ficam os comprovantes guardados e o histórico (`ecozap.db`). |
| `dias_retencao` | Quantos dias guardar antes de apagar (padrão **15**). |
| `formato_padrao` | `pdf`, `jpeg` ou `png`. |
| `tamanho_papel_padrao` | `58mm`, `80mm` ou `a4` (para cupons de texto). |
| `codificacao_texto` | `auto` (tenta UTF-8, CP850 e CP1252) ou fixa uma. |
| `mensagem` | Texto que vai junto. `{nome}` e `{loja}` são substituídos. |
| `whatsapp.provedor` | `simulado`, `cloud_api` ou `evolution` (veja abaixo). |

## Provedores de WhatsApp

- **`simulado`** (padrão): não envia nada, só salva o que *seria* enviado em
  `dados/simulado/`. Use para testar sem risco.
- **`cloud_api`** — **recomendado.** API oficial da Meta (WhatsApp Business Platform).
  - Precisa de conta no *Meta for Developers*, um número dedicado, `token` permanente e `phone_number_id`.
  - ⚠️ **Regra da Meta:** para mandar mensagem a quem **não** falou com a loja nas
    últimas 24 h é obrigatório usar **template aprovado**. Crie um template de
    categoria *Utilidade* com **cabeçalho do tipo Documento** e corpo com `{{1}}`
    (o EcoZap preenche com o nome da loja), por exemplo:
    *"Olá! Segue o comprovante da sua compra na {{1}}. Obrigado por economizar papel 🌱"*.
    Coloque o nome dele em `cloud_api.template`.
  - Cada conversa iniciada pela empresa é cobrada pela Meta (valores baixos para *Utilidade*).
- **`evolution`** — [Evolution API](https://doc.evolution-api.com) (self-hosted, conecta
  via QR Code num WhatsApp comum). Simples e sem custo por mensagem, **mas não é
  oficial**: o número pode ser bloqueado se o WhatsApp entender como spam.

## Uso no dia a dia

- **Chegou impressão** → janela abre por cima de tudo, foco no campo do telefone:
  digite o número e aperte **Enter**. `Esc` = só salvar.
- **Histórico** (janela principal): busca por telefone, nome ou qualquer texto do
  cupom (ex.: valor). Duplo clique = **reenviar** (pode trocar o número/formato).
- **Importar arquivo...**: manda um PDF/imagem que não veio pela impressora.

### Linha de comando

```bat
python -m ecozap                       :: abre a interface
python -m ecozap servico               :: sem tela: envia sozinho se achar o telefone no cupom
python -m ecozap enviar recibo.pdf 11987654321 --formato jpeg
python -m ecozap historico 98765       :: busca no histórico
python -m ecozap reenviar <ID> [novo telefone]
python -m ecozap limpar                :: apaga os mais velhos que dias_retencao
```

## Retenção: 15 dias é suficiente?

Para o objetivo do EcoZap (reenviar se o cliente perder), **sim**. Dois pontos:

- **Fiscal:** quem tem obrigação de guardar documento fiscal (XML da NF-e/NFC-e,
  ~5 anos) é o **sistema emissor**, não o EcoZap. O EcoZap guarda só uma *cópia de
  conveniência*. Confirme com o contador da loja.
- **LGPD:** o histórico tem telefone e nome de clientes. Guardar pouco tempo e
  apagar automaticamente **ajuda** na conformidade (princípio da necessidade).
  Avise o cliente que o comprovante vai pelo WhatsApp e só envie com o consentimento dele.

## Desenvolvimento

```bash
pip install -r requirements.txt pytest
python -m pytest            # testes (rodam em Linux/macOS/Windows, sem WhatsApp real)
```

Estrutura:

```
ecozap/
├── ecozap/
│   ├── __main__.py      linha de comando
│   ├── interface.py     telas (Tkinter)
│   ├── servico.py       regra de negócio: receber, guardar, enviar, reenviar
│   ├── monitor.py       vigia a pasta da impressora virtual
│   ├── conversor.py     PDF / texto / imagem  →  PDF, JPEG, PNG
│   ├── escpos.py        remove comandos de impressora térmica
│   ├── telefone.py      normaliza e acha o telefone no cupom
│   ├── armazenamento.py histórico SQLite + arquivos + limpeza de 15 dias
│   ├── whatsapp.py      provedores: simulado, Cloud API, Evolution
│   └── config.py
├── instalador/          scripts do Windows (impressoras + atalho)
├── tests/
└── docs/obsidian/       anotações do projeto para o seu cofre do Obsidian
```

## Próximos passos (ideias)

- Ícone na bandeja do Windows (`pystray`) em vez de janela.
- Agenda de clientes: autocompletar o telefone pelo nome / CPF do cupom.
- Ler o **QR Code da NFC-e** e mandar o link da consulta junto na mensagem.
- Rodar como serviço do Windows (NSSM) e um painel web para várias lojas/caixas.
- Instalador `.exe` (PyInstaller + Inno Setup) para não depender de Python instalado.
- Relatório mensal "papel economizado" para a loja divulgar (marketing verde).
