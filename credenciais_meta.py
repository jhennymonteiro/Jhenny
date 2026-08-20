#!/usr/bin/env python3
"""
Busca o Pixel ID e o Access Token do Meta Conversions API numa planilha
Google Sheets, pra automacao nao depender de variavel de ambiente nem
guardar segredo em arquivo local/git.

A planilha precisa ter, em qualquer lugar, uma linha com "Token" e
"GraphAPI"/"Graph API" na primeira coluna (o valor do token vai na coluna
seguinte). Precisa estar compartilhada como "qualquer pessoa com o link
pode visualizar".

ATENCAO: qualquer pessoa com o link dessa planilha consegue ler o token de
acesso do Meta. Trate o link da planilha de credenciais com o mesmo cuidado
que trataria a senha em si.
"""

import csv
import io
import re
import urllib.request

import planilha_para_csv as transformador

DEFAULT_CREDENCIAIS_SHEET_ID = "150igoByAapCFZ-7b3KL3z0_gt-PpImgN-uxOeoWAi2Y"
DEFAULT_CREDENCIAIS_GID = "0"

# O Pixel ID de conversoes exato (confirmado pelo cliente por fora da
# planilha). A planilha guarda esse numero como celula numerica e o Google
# Sheets exporta em notacao cientifica ("5,89729E+15"), o que trunca os
# ultimos digitos -- entao nao da pra confiar no valor exportado da planilha
# para esse campo especifico.
PIXEL_ID_PADRAO = "5897290816948180"


def buscar_token_na_planilha(sheet_url: str | None = None) -> str:
    if sheet_url:
        sheet_id, gid = transformador.sheet_id_and_gid_from_url(sheet_url)
    else:
        sheet_id, gid = DEFAULT_CREDENCIAIS_SHEET_ID, DEFAULT_CREDENCIAIS_GID

    conteudo_csv = transformador.baixar_planilha_csv(sheet_id, gid)
    reader = csv.reader(io.StringIO(conteudo_csv))
    for linha in reader:
        if not linha:
            continue
        rotulo = linha[0].strip().lower()
        if "token" in rotulo and re.search(r"graph\s*api", rotulo):
            for valor in linha[1:]:
                valor = valor.strip()
                if valor:
                    return valor
    raise ValueError(
        "Nao encontrei uma linha com 'Token' + 'GraphAPI' na planilha de credenciais."
    )


def buscar_credenciais(sheet_url: str | None = None) -> tuple[str, str]:
    """Retorna (pixel_id, access_token)."""
    token = buscar_token_na_planilha(sheet_url)
    return PIXEL_ID_PADRAO, token
