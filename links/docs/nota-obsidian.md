---
projeto: Portal de Links RCM
tags: [rcm, links, qrcode, cloudflare]
criado: 2026-10-31
---
# Portal de Links RCM

- Substitui o linktr.ee/rcmsistemas por página própria.
- Código: repo `rdgen`, pasta `links/`. Build: `python links/build.py`.
- Um JSON por cliente em `links/perfis/`. Slug = URL.
- QR aponta para `/q/<slug>` (redirect 302) → dá para mudar destino sem reimprimir.
- Hospedagem sugerida: Cloudflare Pages (grátis, headers de segurança via `_headers`).
- Domínio sugerido: `links.rcmsistemas.com.br` (início) e depois `rcm.bio` para clientes.

## Pendências
- [ ] Trocar placeholders (WhatsApp, ID Google, URLs reais)
- [ ] Logo da RCM em `perfis/imagens/`
- [ ] Registrar domínio e configurar Cloudflare Pages
