#!/usr/bin/env python3
"""Gerador estático do portal de links da RCM.

Lê cada perfil em perfis/*.json e gera em dist/:
  /<slug>/index.html   página de links (HTML único, sem dependências externas)
  /<slug>/qr.svg|png   QR code apontando para o encurtador /q/<slug>
  /<slug>/contato.vcf  cartão de contato (opcional)
  /_redirects          encurtador /q/<slug> (Cloudflare Pages / Netlify)
  /_headers            cabeçalhos de segurança (CSP com hash, HSTS etc.)

Uso: python build.py [--dominio https://links.exemplo.com.br]
"""

import argparse
import base64
import hashlib
import html
import json
import re
import shutil
import sys
from pathlib import Path
from string import Template
from urllib.parse import urlparse

import segno

RAIZ = Path(__file__).resolve().parent
PERFIS = RAIZ / "perfis"
MODELOS = RAIZ / "modelos"
DIST = RAIZ / "dist"

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
COR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ESQUEMAS_OK = {"https", "mailto", "tel"}
AVATAR_EXT = {".png", ".jpg", ".jpeg", ".webp"}
SLUGS_RESERVADOS = {"q", "assets", "admin", "api", "404"}


class ErroPerfil(Exception):
    pass


def esc(texto):
    return html.escape(str(texto), quote=True)


def validar_url(url, onde):
    p = urlparse(url)
    if p.scheme not in ESQUEMAS_OK:
        raise ErroPerfil(f"{onde}: URL deve usar https, mailto ou tel ({url!r})")
    if p.scheme == "https" and not p.netloc:
        raise ErroPerfil(f"{onde}: URL sem domínio ({url!r})")
    if any(c in url for c in "\"'<>\n\r\t "):
        raise ErroPerfil(f"{onde}: URL contém caracteres inválidos ({url!r})")
    return url


def carregar_icones():
    icones = {}
    for arq in sorted((MODELOS / "icones").glob("*.svg")):
        icones[arq.stem] = arq.read_text(encoding="utf-8").strip()
    return icones


def validar_perfil(dados, arquivo, icones):
    slug = dados.get("slug", "")
    if not SLUG_RE.match(slug) or slug in SLUGS_RESERVADOS:
        raise ErroPerfil(f"{arquivo}: slug inválido ou reservado ({slug!r})")
    if not dados.get("nome"):
        raise ErroPerfil(f"{arquivo}: campo 'nome' é obrigatório")
    tema = dados.setdefault("tema", {})
    for chave, padrao in (("primaria", "#2563eb"), ("secundaria", "#06b6d4")):
        cor = tema.setdefault(chave, padrao)
        if not COR_RE.match(cor):
            raise ErroPerfil(f"{arquivo}: cor {chave} inválida ({cor!r}), use #RRGGBB")
    links = dados.get("links") or []
    if not links:
        raise ErroPerfil(f"{arquivo}: informe ao menos um link")
    for i, link in enumerate(links + dados.get("sociais", [])):
        onde = f"{arquivo} link #{i + 1}"
        validar_url(link.get("url", ""), onde)
        if link.get("icone", "link") not in icones:
            raise ErroPerfil(f"{onde}: ícone desconhecido ({link.get('icone')!r})")
    avatar = dados.get("avatar")
    if avatar:
        caminho = (PERFIS / "imagens" / avatar).resolve()
        if caminho.parent != (PERFIS / "imagens").resolve() or caminho.suffix.lower() not in AVATAR_EXT:
            raise ErroPerfil(f"{arquivo}: avatar deve ser png/jpg/webp em perfis/imagens/")
        if not caminho.is_file():
            raise ErroPerfil(f"{arquivo}: avatar não encontrado ({avatar})")
    return dados


def iniciais(nome, sigla=None):
    if sigla:
        return sigla[:3].upper()
    partes = [p for p in re.split(r"\s+", nome) if p]
    return "".join(p[0] for p in partes[:2]).upper()


def html_link(link, icones):
    classe = "link destaque" if link.get("destaque") else "link"
    sub = f'<span class="sub">{esc(link["descricao"])}</span>' if link.get("descricao") else ""
    return (
        f'<li><a class="{classe}" href="{esc(link["url"])}" rel="noopener">'
        f'<span class="ico">{icones[link.get("icone", "link")]}</span>'
        f'<span class="txt"><span class="tit">{esc(link["titulo"])}</span>{sub}</span>'
        f'<span class="seta" aria-hidden="true">{icones["seta"]}</span></a></li>'
    )


def html_social(link, icones):
    return (
        f'<li><a href="{esc(link["url"])}" rel="noopener" aria-label="{esc(link["titulo"])}" '
        f'title="{esc(link["titulo"])}">{icones[link.get("icone", "link")]}</a></li>'
    )


def gerar_vcard(dados):
    c = dados.get("contato")
    if not c:
        return None

    def limpo(v):
        return re.sub(r"[\r\n;,]", " ", str(v)).strip()

    linhas = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{limpo(dados['nome'])}", f"ORG:{limpo(dados['nome'])}"]
    if c.get("telefone"):
        linhas.append(f"TEL;TYPE=WORK,VOICE:{limpo(c['telefone'])}")
    if c.get("email"):
        linhas.append(f"EMAIL;TYPE=WORK:{limpo(c['email'])}")
    if c.get("site"):
        linhas.append(f"URL:{limpo(c['site'])}")
    linhas.append("END:VCARD")
    return "\r\n".join(linhas) + "\r\n"


def sha256_csp(conteudo):
    return "'sha256-" + base64.b64encode(hashlib.sha256(conteudo.encode()).digest()).decode() + "'"


def favicon(dados):
    p, s = dados["tema"]["primaria"], dados["tema"]["secundaria"]
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="{p}"/>'
        f'<stop offset="1" stop-color="{s}"/></linearGradient></defs>'
        '<rect width="64" height="64" rx="16" fill="url(#g)"/>'
        '<text x="32" y="41" font-family="system-ui,sans-serif" font-size="26" font-weight="700" '
        f'fill="#fff" text-anchor="middle">{esc(iniciais(dados["nome"], dados.get("sigla")))}</text></svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def montar_pagina(dados, dominio, icones, css, js, modelo):
    slug = dados["slug"]
    url_pagina = f"{dominio}/{slug}/"
    avatar_html = (
        f'<img class="avatar" src="avatar{Path(dados["avatar"]).suffix.lower()}" '
        f'alt="{esc(dados["nome"])}" width="104" height="104">'
        if dados.get("avatar")
        else f'<div class="avatar mono" aria-hidden="true">{esc(iniciais(dados["nome"], dados.get("sigla")))}</div>'
    )
    selo = icones["verificado"] if dados.get("verificado") else ""
    contato_btn = (
        f'<a class="acao" href="contato.vcf" download>{icones["contato"]}<span>Salvar contato</span></a>'
        if dados.get("contato")
        else ""
    )
    # CSS do tema vai num <style> próprio para o hash do CSS base ser igual em todos os perfis
    css_tema = f':root{{--p:{dados["tema"]["primaria"]};--s:{dados["tema"]["secundaria"]}}}'
    csp = (
        "default-src 'none'; "
        f"style-src {sha256_csp(css)} {sha256_csp(css_tema)}; "
        f"script-src {sha256_csp(js)}; "
        "img-src 'self' data:; manifest-src 'self'; "
        "base-uri 'none'; form-action 'none'; object-src 'none'"
    )
    pagina = Template(modelo).substitute(
        csp=esc(csp),
        titulo=esc(dados["nome"]),
        descricao=esc(dados.get("bio", "")),
        url=esc(url_pagina),
        cor=esc(dados["tema"]["primaria"]),
        favicon=favicon(dados),
        css=css,
        css_tema=css_tema,
        js=js,
        avatar=avatar_html,
        nome=esc(dados["nome"]),
        selo=selo,
        bio=esc(dados.get("bio", "")),
        links="".join(html_link(l, icones) for l in dados["links"]),
        sociais="".join(html_social(l, icones) for l in dados.get("sociais", [])),
        contato_btn=contato_btn,
        icone_compartilhar=icones["compartilhar"],
        icone_qr=icones["qr"],
        rodape=esc(dados.get("rodape", "RCM Sistemas")),
    )
    return pagina, csp


def gerar_qr(dados, dominio, destino):
    qr = segno.make(f"{dominio}/q/{dados['slug']}", error="h")
    escuro = "#0b1220"
    qr.save(destino / "qr.svg", scale=10, border=2, dark=escuro, light="#ffffff")
    qr.save(destino / "qr.png", scale=20, border=2, dark=escuro, light="#ffffff")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dominio", help="URL base pública (sobrescreve config.json)")
    args = ap.parse_args()

    config = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
    dominio = (args.dominio or config["dominio"]).rstrip("/")
    if urlparse(dominio).scheme != "https":
        sys.exit("Erro: o domínio deve começar com https://")

    icones = carregar_icones()
    css = (MODELOS / "estilo.css").read_text(encoding="utf-8")
    js = (MODELOS / "app.js").read_text(encoding="utf-8")
    modelo = (MODELOS / "pagina.html").read_text(encoding="utf-8")

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()

    redirects = []
    headers = [
        "/*",
        "  Strict-Transport-Security: max-age=63072000; includeSubDomains; preload",
        "  X-Content-Type-Options: nosniff",
        "  X-Frame-Options: DENY",
        "  Referrer-Policy: strict-origin-when-cross-origin",
        "  Permissions-Policy: camera=(), microphone=(), geolocation=(), interest-cohort=()",
        "  Cross-Origin-Opener-Policy: same-origin",
        "",
    ]
    slugs, erros = set(), []

    for arquivo in sorted(PERFIS.glob("*.json")):
        try:
            dados = validar_perfil(json.loads(arquivo.read_text(encoding="utf-8")), arquivo.name, icones)
        except (ErroPerfil, json.JSONDecodeError) as e:
            erros.append(str(e))
            continue
        slug = dados["slug"]
        if slug in slugs:
            erros.append(f"{arquivo.name}: slug duplicado ({slug})")
            continue
        slugs.add(slug)

        destino = DIST / slug
        destino.mkdir()
        pagina, csp = montar_pagina(dados, dominio, icones, css, js, modelo)
        (destino / "index.html").write_text(pagina, encoding="utf-8")
        if dados.get("avatar"):
            shutil.copy(PERFIS / "imagens" / dados["avatar"], destino / f"avatar{Path(dados['avatar']).suffix.lower()}")
        vcard = gerar_vcard(dados)
        if vcard:
            (destino / "contato.vcf").write_text(vcard, encoding="utf-8")
        gerar_qr(dados, dominio, destino)

        # O QR aponta para /q/<slug>: dá para trocar o destino sem reimprimir o QR
        qr_destino = dados.get("qr_destino") or f"/{slug}/?utm_source=qrcode&utm_medium=offline"
        if not qr_destino.startswith("/"):
            validar_url(qr_destino, f"{arquivo.name} qr_destino")
        redirects.append(f"/q/{slug} {qr_destino} 302")
        headers += [f"/{slug}/*", f"  Content-Security-Policy: {csp}; frame-ancestors 'none'", ""]
        print(f"  ok  /{slug}/  ->  QR: {dominio}/q/{slug}")

    if erros:
        print("\nErros encontrados:", *erros, sep="\n  - ")
        sys.exit(1)

    padrao = config.get("perfil_padrao", "rcm")
    redirects.append(f"/ /{padrao}/ 302")
    (DIST / "_redirects").write_text("\n".join(redirects) + "\n", encoding="utf-8")
    (DIST / "_headers").write_text("\n".join(headers), encoding="utf-8")
    shutil.copy(MODELOS / "404.html", DIST / "404.html")
    (DIST / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    print(f"\n{len(slugs)} página(s) gerada(s) em {DIST}")


if __name__ == "__main__":
    main()
