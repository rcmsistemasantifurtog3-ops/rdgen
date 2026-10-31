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
