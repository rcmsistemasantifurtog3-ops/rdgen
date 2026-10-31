CUPOM = """\
        MERCADINHO BOM PRECO LTDA
     CNPJ 12.345.678/0001-90
   Rua das Flores, 100 - Centro
------------------------------------------
        RECIBO DE PAGAMENTO
------------------------------------------
Item  Descricao            Qtd   Valor
001   Arroz 5kg             1    27,90
002   Feijão carioca 1kg    2    15,80
003   Café 500g             1    18,50
------------------------------------------
TOTAL                       R$   62,20
Forma de pagamento: PIX
CPF consumidor: 123.456.789-09
Cliente: Maria Souza  Cel: (11) 98765-4321
------------------------------------------
   Obrigado pela preferência!
"""

# mesmo cupom como um PDV mandaria para a térmica (ESC/POS, cp850)
ESCPOS = (b"\x1b@" + b"\x1ba\x01" + b"\x1bE\x01" + "MERCADINHO BOM PRECO\n".encode("cp850")
          + b"\x1bE\x00" + b"\x1ba\x00" + "Feijão carioca   15,80\n".encode("cp850")
          + "Tel: 21 99876-5432\n".encode("cp850")
          + b"\x1dk\x04123456\x00"          # código de barras
          + b"\x1d(k\x03\x001A2"            # pedaço de QR code
          + b"\n\n\x1dV\x41\x03")            # corte


def com_dv(base43: str) -> str:
    """Completa uma chave de acesso de 43 dígitos com o dígito verificador."""
    soma, peso = 0, 2
    for d in reversed(base43):
        soma += int(d) * peso
        peso = 2 if peso == 9 else peso + 1
    dv = 11 - soma % 11
    return base43 + str(0 if dv >= 10 else dv)


CHAVE = com_dv("3526101234567800019065001000001234100001234")   # modelo 65 = NFC-e
URL_QR = f"https://www.nfce.fazenda.sp.gov.br/qrcode?p={CHAVE}|2|1|1|ABCDEF"


def escpos_nfce(cpf: str = "CPF: 529.982.247-25", tel_linha: str = "") -> bytes:
    """Cupom NFC-e como um PDV mandaria: texto + QR Code (GS ( k)."""
    qr = URL_QR.encode()
    guardar = b"\x1d(k" + (len(qr) + 3).to_bytes(2, "little") + b"1P0" + qr
    imprimir = b"\x1d(k\x03\x001Q0"
    chave_fmt = " ".join(CHAVE[i:i + 4] for i in range(0, 44, 4))
    corpo = (f"PADARIA PAO QUENTE\nDocumento Auxiliar da NFC-e\nTOTAL R$ 12,50\n"
             f"CONSUMIDOR - {cpf}\n{tel_linha}\nConsulte pela Chave de Acesso em\n"
             f"www.nfce.fazenda.sp.gov.br/consulta\n{chave_fmt}\n")
    return b"\x1b@" + corpo.encode("cp850") + b"\x1ba\x01" + guardar + imprimir + b"\n\x1dV\x00"
