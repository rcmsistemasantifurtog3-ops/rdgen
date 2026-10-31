"""Desenha o ícone do EcoZap (folha verde) com Pillow - sem arquivo de imagem."""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

VERDE = (46, 125, 50)
VERDE_CLARO = (200, 230, 201)


def pontos_folha(cx: float, cy: float, tamanho: float, angulo: float = -45) -> list[tuple]:
    """Contorno de uma folha (ponta para cima-direita), centrada em (cx, cy)."""
    pts = []
    passos = 40
    for lado in (1, -1):
        for i in range(passos + 1):
            t = i / passos if lado == 1 else 1 - i / passos
            eixo = (t - 0.5) * tamanho                  # ao longo do comprimento
            largura = 0.33 * tamanho * math.sin(math.pi * t) ** 0.9 * (1 - 0.25 * t)
            pts.append((eixo, lado * largura))
    rad = math.radians(angulo)
    c, s = math.cos(rad), math.sin(rad)
    return [(cx + x * c - y * s, cy + x * s + y * c) for x, y in pts]


def desenhar_folha(d: ImageDraw.ImageDraw, cx, cy, tamanho, cor, cor_nervura, angulo=-45):
    d.polygon(pontos_folha(cx, cy, tamanho, angulo), fill=cor)
    rad = math.radians(angulo)
    ini = (cx - 0.62 * tamanho * math.cos(rad), cy - 0.62 * tamanho * math.sin(rad))
    fim = (cx + 0.38 * tamanho * math.cos(rad), cy + 0.38 * tamanho * math.sin(rad))
    d.line([ini, fim], fill=cor_nervura, width=max(1, int(tamanho / 22)))


def gerar(tamanho: int = 256, pausado: bool = False) -> Image.Image:
    """Ícone redondo: círculo verde (cinza se pausado) com folha clara."""
    escala = 4  # desenha grande e reduz, para ficar sem serrilhado
    t = tamanho * escala
    img = Image.new("RGBA", (t, t), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fundo = (120, 120, 120) if pausado else VERDE
    d.ellipse((0, 0, t - 1, t - 1), fill=fundo)
    desenhar_folha(d, t * 0.52, t * 0.48, t * 0.62, VERDE_CLARO, fundo)
    return img.resize((tamanho, tamanho), Image.LANCZOS)


def salvar_ico(caminho: str) -> None:
    gerar(256).save(caminho, format="ICO", sizes=[(16, 16), (24, 24), (32, 32),
                                                   (48, 48), (64, 64), (128, 128), (256, 256)])
