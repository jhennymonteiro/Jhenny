#!/usr/bin/env python3
"""
Baixa a planilha de Leads da SkinPet (Google Sheets) e transforma no CSV
adaptado que o Meta espera (mesmo formato de Controle_de_Leads_-_SkinPet_adaptado.csv).

Fonte dos eventos: aba HISTÓRICO (log de movimentação de fase de cada lead,
uma linha por transição: Data da Alteração, Nome, WhatsApp, Fase anterior,
nova fase). O valor da venda não está no HISTÓRICO, entao e' buscado na aba
principal (SkinPet) cruzando pelo WhatsApp.

Uso:
  python3 planilha_para_csv.py > Controle_de_Leads_-_SkinPet_adaptado.csv
  python3 planilha_para_csv.py --sheet-url "https://docs.google.com/spreadsheets/d/XXX/edit#gid=0" -o saida.csv

A planilha precisa estar compartilhada como "qualquer pessoa com o link pode
visualizar" para o download funcionar sem autenticação.
"""

import argparse
import csv
import io
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime

DEFAULT_SHEET_ID = "1kYnu0PDKn9oIOOQqYi2oN4qCt9C-da0sS2P4HuXBbTE"
DEFAULT_GID = "0"
ABA_HISTORICO = "HISTÓRICO"

COLUNAS_SAIDA = [
    "email", "email", "email",
    "phone", "phone", "phone",
    "madid", "fn", "ln", "zip", "ct", "st", "country",
    "dob", "doby", "gen", "age",
    "event_name", "event_time", "value", "currency",
]

FASE_NEGOCIO_FECHADO = "Negócio Fechado"


def sheet_id_and_gid_from_url(url: str) -> tuple[str, str]:
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if not m:
        raise ValueError(f"Nao consegui extrair o ID da planilha de: {url}")
    sheet_id = m.group(1)
    gid_match = re.search(r"[#&?]gid=(\d+)", url)
    gid = gid_match.group(1) if gid_match else "0"
    return sheet_id, gid


def baixar_planilha_csv(sheet_id: str, gid: str) -> str:
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    with urllib.request.urlopen(url) as response:
        return response.read().decode("utf-8")


def baixar_aba_por_nome(sheet_id: str, nome_aba: str) -> str:
    """Baixa uma aba pelo nome (nao precisa saber o gid)."""
    nome_url = urllib.parse.quote(nome_aba)
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={nome_url}"
    with urllib.request.urlopen(url) as response:
        return response.read().decode("utf-8")


def parse_valor(valor_str: str) -> str:
    """'R$ 1.230,00' -> '1230.00'"""
    numero = valor_str.replace("R$", "").strip()
    numero = numero.replace(".", "").replace(",", ".")
    try:
        return f"{float(numero):.2f}"
    except ValueError:
        return ""


def parse_data(data_str: str) -> str:
    """'18/08/2026 17:20:11' -> '2026-08-18T17:20:11Z' (aceita tambem so a data, sem hora)"""
    data_str = data_str.strip()
    for formato in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(data_str, formato)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    raise ValueError(f"Formato de data nao reconhecido: {data_str!r}")


def normalizar_telefone(numero_str: str) -> str:
    """Deixa so digitos e garante DDI 55 + DDD + 9 digitos."""
    digitos = re.sub(r"\D", "", numero_str)
    if digitos.startswith("55") and len(digitos) in (12, 13):
        sem_ddi = digitos[2:]
    else:
        sem_ddi = digitos
    if len(sem_ddi) == 10:
        # DDD (2) + 8 digitos -> falta o 9 do celular
        sem_ddi = sem_ddi[:2] + "9" + sem_ddi[2:]
    return "55" + sem_ddi


def separar_nome(nome_completo: str) -> tuple[str, str]:
    partes = nome_completo.strip().split(" ", 1)
    fn = partes[0] if partes else ""
    ln = partes[1] if len(partes) > 1 else ""
    return fn, ln


def transformar_linha(linha: dict) -> dict | None:
    nome = (linha.get("Nome") or "").strip()
    whatsapp = (linha.get("WhatsApp") or "").strip()
    fase = (linha.get("Fase") or "").strip()
    valor = (linha.get("Valor estimado") or "").strip()
    data_registro = (linha.get("data de registro aut") or "").strip()

    if not nome or not whatsapp or not data_registro:
        return None

    fn, ln = separar_nome(nome)
    telefone = normalizar_telefone(whatsapp)
    ddd_numero = telefone[2:]  # sem o 55, para os formatos "bonitos"

    event_name = "Purchase" if fase == FASE_NEGOCIO_FECHADO else "Lead"

    return {
        "email": "", "email.1": "", "email.2": "",
        "phone": f"+55 ({ddd_numero[:2]}) {ddd_numero[2:7]}-{ddd_numero[7:]}",
        "phone.1": f"55-({ddd_numero[:2]})-{ddd_numero[2:7]}-{ddd_numero[7:]}",
        "phone.2": f"+{telefone}",
        "madid": "", "fn": fn, "ln": ln, "zip": "", "ct": "", "st": "",
        "country": "BR", "dob": "", "doby": "", "gen": "", "age": "",
        "event_name": event_name,
        "event_time": parse_data(data_registro),
        "value": parse_valor(valor),
        "currency": "BRL",
    }


def construir_mapa_valores(conteudo_csv_principal: str) -> dict[str, str]:
    """Le a aba principal e monta {telefone_normalizado: valor_estimado}."""
    reader = csv.DictReader(io.StringIO(conteudo_csv_principal))
    mapa = {}
    for linha in reader:
        whatsapp = (linha.get("WhatsApp") or "").strip()
        valor = (linha.get("Valor estimado") or "").strip()
        if not whatsapp:
            continue
        telefone = normalizar_telefone(whatsapp)
        mapa[telefone] = parse_valor(valor) if valor else ""
    return mapa


FASE_SEM_FASE = "Sem fase"


def transformar_linha_historico(linha: dict, mapa_valores: dict[str, str]) -> dict | None:
    nome = (linha.get("Nome") or "").strip()
    whatsapp = (linha.get("WhatsApp") or "").strip()
    fase_anterior = (linha.get("Fase anterior") or "").strip()
    nova_fase = (linha.get("nova fase") or "").strip()
    data_alteracao = (linha.get("Data da Alteração") or "").strip()

    if not nome or not whatsapp or not nova_fase or not data_alteracao:
        return None

    # So dispara evento na primeira entrada (Lead) e no fechamento do negocio
    # (Purchase). Transicoes intermediarias (Conexao, FollowUp, Oportunidade,
    # Negocio Perdido) nao geram evento nenhum.
    if nova_fase == FASE_NEGOCIO_FECHADO:
        event_name = "Purchase"
    elif fase_anterior == FASE_SEM_FASE or not fase_anterior:
        event_name = "Lead"
    else:
        return None

    fn, ln = separar_nome(nome)
    telefone = normalizar_telefone(whatsapp)
    ddd_numero = telefone[2:]
    valor = mapa_valores.get(telefone, "")

    return {
        "email": "", "email.1": "", "email.2": "",
        "phone": f"+55 ({ddd_numero[:2]}) {ddd_numero[2:7]}-{ddd_numero[7:]}",
        "phone.1": f"55-({ddd_numero[:2]})-{ddd_numero[2:7]}-{ddd_numero[7:]}",
        "phone.2": f"+{telefone}",
        "madid": "", "fn": fn, "ln": ln, "zip": "", "ct": "", "st": "",
        "country": "BR", "dob": "", "doby": "", "gen": "", "age": "",
        "event_name": event_name,
        "event_time": parse_data(data_alteracao),
        "value": valor,
        "currency": "BRL" if valor else "",
    }


def gerar_csv(linhas_transformadas: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(COLUNAS_SAIDA)
    chaves = ["email", "email.1", "email.2", "phone", "phone.1", "phone.2",
              "madid", "fn", "ln", "zip", "ct", "st", "country", "dob", "doby",
              "gen", "age", "event_name", "event_time", "value", "currency"]
    for linha in linhas_transformadas:
        writer.writerow([linha.get(k, "") for k in chaves])
    return buf.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sheet-url", default=None, help="URL completa da planilha (com gid). Se omitido, usa a planilha padrao da SkinPet.")
    parser.add_argument("-o", "--output", default=None, help="Arquivo de saida. Se omitido, imprime no stdout.")
    args = parser.parse_args()

    if args.sheet_url:
        sheet_id, gid = sheet_id_and_gid_from_url(args.sheet_url)
    else:
        sheet_id, gid = DEFAULT_SHEET_ID, DEFAULT_GID

    conteudo_principal = baixar_planilha_csv(sheet_id, gid)
    mapa_valores = construir_mapa_valores(conteudo_principal)

    conteudo_historico = baixar_aba_por_nome(sheet_id, ABA_HISTORICO)
    reader = csv.DictReader(io.StringIO(conteudo_historico))

    linhas_transformadas = []
    ignoradas = 0
    for linha in reader:
        resultado = transformar_linha_historico(linha, mapa_valores)
        if resultado is None:
            ignoradas += 1
            continue
        linhas_transformadas.append(resultado)

    saida_csv = gerar_csv(linhas_transformadas)

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="") as f:
            f.write(saida_csv)
        print(f"{len(linhas_transformadas)} linhas convertidas ({ignoradas} ignoradas por dados faltando) -> {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(saida_csv)
        print(f"{len(linhas_transformadas)} linhas convertidas ({ignoradas} ignoradas por dados faltando)", file=sys.stderr)


if __name__ == "__main__":
    main()
