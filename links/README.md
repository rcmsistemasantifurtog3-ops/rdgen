# Portal de Links RCM

Gerador estático de páginas de links (estilo linktr.ee) para a RCM e para clientes.
Cada perfil é um arquivo JSON; o build gera HTML puro, QR code e cartão de contato.

## Por que estático?

- **Seguro**: sem banco, sem servidor, sem login para invadir. Só arquivos HTML.
- **Leve**: cada página tem ~20 KB, zero requisições externas (sem fontes, CDN ou rastreadores).
- **Escala**: 10 ou 10.000 clientes custam o mesmo (Cloudflare Pages é gratuito).

## Estrutura

```
links/
├── build.py            # gerador
├── config.json         # domínio e perfil padrão
├── perfis/             # um .json por cliente  ← você mexe aqui
│   └── imagens/        # logos (png/jpg/webp)
└── modelos/            # HTML, CSS, JS e ícones
```

## Uso

```bash
pip install -r requirements.txt
python build.py                                   # usa o domínio do config.json
python build.py --dominio https://rcm.bio         # ou sobrescreve
cd dist && python -m http.server 8000             # testar em http://localhost:8000/rcm/
```

## Criar a página de um cliente

1. Copie `perfis/cliente-exemplo.json` para `perfis/<cliente>.json`.
2. Ajuste `slug` (vira a URL: `/<slug>/`), nome, cores e links.
3. (Opcional) coloque a logo em `perfis/imagens/` e informe em `"avatar": "logo.webp"`.
4. Rode o build. O QR fica em `dist/<slug>/qr.svg` (vetor, para gráfica) e `qr.png`.

### Campos do perfil

| Campo | Obrigatório | Descrição |
|---|---|---|
| `slug` | sim | minúsculas, números e hífen (ex.: `padaria-joao`) |
| `nome` | sim | nome exibido |
| `sigla` | não | texto do monograma quando não há logo (até 3 letras) |
| `bio` | não | frase abaixo do nome |
| `avatar` | não | arquivo em `perfis/imagens/` |
| `verificado` | não | `true` mostra o selo azul |
| `tema.primaria` / `tema.secundaria` | não | cores `#RRGGBB` do degradê |
| `links[]` | sim | `titulo`, `url`, `icone`, `descricao`, `destaque` |
| `sociais[]` | não | ícones redondos abaixo da bio |
| `contato` | não | `telefone`, `email`, `site` → gera botão "Salvar contato" (.vcf) |
| `qr_destino` | não | muda para onde o QR aponta **sem reimprimir** |
| `rodape` | não | texto do rodapé |

Ícones disponíveis: `whatsapp`, `novidades`, `avaliacao`, `instagram`, `linkedin`, `youtube`,
`site`, `produtos`, `software`, `email`, `telefone`, `mapa`, `link`.
Para adicionar um, basta criar um `.svg` em `modelos/icones/`.

### Links úteis

- **WhatsApp com mensagem pronta**: `https://wa.me/55DDDNUMERO?text=Quero%20receber%20novidades`
- **Avaliação no Google**: no Perfil da Empresa → "Pedir avaliações" → copie o link `g.page/r/.../review`.

## QR code dinâmico (encurtador)

O QR **não** aponta direto para a página, e sim para `/q/<slug>`, que redireciona (arquivo `_redirects`).
Assim dá para trocar o destino depois sem reimprimir nada, e os acessos chegam com
`utm_source=qrcode` para medir quantas pessoas vieram do QR.

## Segurança

- CSP estrita com hash: só roda o CSS/JS gerado pelo build (nada de scripts injetados).
- HSTS, `X-Frame-Options: DENY`, `nosniff`, `Permissions-Policy` (arquivo `_headers`).
- Todo texto do JSON é escapado; URLs só aceitam `https:`, `mailto:` e `tel:` (bloqueia `javascript:`).
- Logos SVG não são aceitas (podem conter script); só png/jpg/webp.
- Sem cookies e sem rastreadores → mais simples para LGPD.

## Publicar (Cloudflare Pages, gratuito)

1. Cloudflare → Workers & Pages → Create → Pages → conectar este repositório.
2. Build command: `pip install -r links/requirements.txt && python links/build.py`
3. Build output: `links/dist`
4. Custom domain: aponte o domínio escolhido.

Cada `git push` com um perfil novo publica a página em ~30 s.
