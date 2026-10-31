"""Limpa comandos ESC/POS (impressora térmica) e devolve só o texto do cupom.

Sistemas de PDV costumam mandar o cupom "cru" (RAW) para a impressora
térmica, misturando texto com comandos de negrito, corte de papel, gaveta,
QR Code etc. Aqui jogamos fora os comandos e ficamos com o texto.
"""
from __future__ import annotations

ESC, GS, FS, DLE = 0x1B, 0x1D, 0x1C, 0x10

# Quantos bytes de parâmetro cada comando ESC <x> usa (os mais comuns).
_ESC_PARAMS = {
    ord("!"): 1, ord("-"): 1, ord("E"): 1, ord("G"): 1, ord("M"): 1,
    ord("a"): 1, ord("d"): 1, ord("J"): 1, ord("t"): 1, ord("R"): 1,
    ord("{"): 1, ord("V"): 1, ord("r"): 1, ord("c"): 2, ord("p"): 3,
    ord("@"): 0, ord("2"): 0, ord("3"): 1, ord("i"): 0, ord("m"): 0,
    ord("$"): 2, ord("\\"): 2, ord("e"): 1, ord(" "): 1,
}
_GS_PARAMS = {
    ord("!"): 1, ord("B"): 1, ord("H"): 1, ord("f"): 1, ord("h"): 1,
    ord("w"): 1, ord("V"): None, ord("L"): 2, ord("W"): 2, ord("a"): 1,
    ord("r"): 1, ord("b"): 1, ord("I"): 1,
}


MARCA_QR = "[[QR:{}]]"  # o conversor troca essa marca pela imagem do QR Code


def limpar(dados: bytes) -> bytes:
    """Remove comandos ESC/POS, mantendo texto, quebras de linha e tabs.

    O QR Code (ex.: NFC-e) não é jogado fora: onde a impressora o imprimiria,
    entra uma linha ``[[QR:conteúdo]]`` para o conversor redesenhar.
    """
    saida = bytearray()
    qr_guardado = b""
    i, n = 0, len(dados)
    while i < n:
        b = dados[i]
        if b == ESC and i + 1 < n:
            cmd = dados[i + 1]
            if cmd == ord("*") and i + 4 < n:  # imagem de bits
                m, nl, nh = dados[i + 2], dados[i + 3], dados[i + 4]
                largura = nl + nh * 256
                i += 5 + largura * (3 if m in (32, 33) else 1)
                continue
            i += 2 + _ESC_PARAMS.get(cmd, 0)
            continue
        if b == GS and i + 1 < n:
            cmd = dados[i + 1]
            if cmd == ord("V"):  # corte: GS V m [n]
                m = dados[i + 2] if i + 2 < n else 0
                i += 4 if m in (65, 66, 97, 98) else 3
                continue
            if cmd == ord("k") and i + 2 < n:  # código de barras
                m = dados[i + 2]
                if m <= 6:  # termina com NUL
                    fim = dados.find(b"\x00", i + 3)
                    i = n if fim < 0 else fim + 1
                elif i + 3 < n:
                    i += 4 + dados[i + 3]
                else:
                    i = n
                continue
            if cmd in (ord("("), ord("8")) and i + 4 < n:  # QR Code, imagens...
                if cmd == ord("("):
                    tam = dados[i + 3] + dados[i + 4] * 256
                    if dados[i + 2] == ord("k") and i + 6 < n and dados[i + 5] == 49:
                        fn = dados[i + 6]
                        if fn == 80:    # guarda os dados do QR Code
                            qr_guardado = dados[i + 8:i + 5 + tam]
                        elif fn == 81 and qr_guardado:  # imprime o QR guardado
                            conteudo = qr_guardado.decode("latin-1").replace("]]", "] ]")
                            saida += b"\n" + MARCA_QR.format(conteudo).encode("latin-1") + b"\n"
                            qr_guardado = b""
                    i += 5 + tam
                else:  # GS 8 L p1 p2 p3 p4
                    tam = int.from_bytes(dados[i + 3:i + 7], "little")
                    i += 7 + tam
                continue
            if cmd == ord("v") and i + 7 < n:  # GS v 0 m xL xH yL yH (raster)
                x = dados[i + 4] + dados[i + 5] * 256
                y = dados[i + 6] + dados[i + 7] * 256
                i += 8 + x * y
                continue
            i += 2 + (_GS_PARAMS.get(cmd) or 0)
            continue
        if b in (FS, DLE) and i + 1 < n:
            i += 3 if b == DLE else 2
            continue
        if b in (0x0A, 0x09) or b >= 0x20:
            saida.append(b)
        elif b == 0x0D:
            pass  # CR: ignorado, usamos só LF
        elif b == 0x0C:
            saida.append(0x0A)  # form feed vira quebra de linha
        i += 1
    return bytes(saida)


def parece_escpos(dados: bytes) -> bool:
    return bytes([ESC]) in dados[:2048] or bytes([GS]) in dados[:2048]
