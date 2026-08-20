#!/usr/bin/env python3
"""
Pipeline completo: planilha do Google Sheets -> CSV adaptado -> envio pro Meta (CAPI).

So envia eventos novos (linhas que ainda nao foram enviadas em uma execucao
anterior, controlado por um arquivo de estado local). Se uma linha ja enviada
mudar de fase (ex: virou "Negocio Fechado"), ela e considerada nova e reenviada
com o event_name atualizado.

Uso:
  export META_PIXEL_ID="123456789012345"
  export META_ACCESS_TOKEN="seu_token"
  python3 enviar_leads_meta.py                  # roda de verdade
  python3 enviar_leads_meta.py --dry-run         # so mostra o que seria enviado
  python3 enviar_leads_meta.py --test-event-code TEST12345
  python3 enviar_leads_meta.py --reenviar-tudo   # ignora o estado e reenvia tudo
"""

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

import planilha_para_csv as transformador
import enviar_eventos_capi as capi

ESTADO_PATH = Path(__file__).parent / ".estado_leads_enviados.json"
CSV_SAIDA_PATH = Path(__file__).parent / "Controle_de_Leads_-_SkinPet_adaptado.csv"

# O Meta rejeita o lote inteiro se qualquer evento for mais velho que isso
# (regra da Conversions API para eventos enviados via servidor).
JANELA_MAX_SEGUNDOS = 7 * 24 * 60 * 60


def chave_linha(linha: dict) -> str:
    base = "|".join([linha.get("phone.2", ""), linha.get("event_name", ""), linha.get("event_time", ""), linha.get("value", "")])
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def carregar_estado() -> set[str]:
    if not ESTADO_PATH.exists():
        return set()
    with open(ESTADO_PATH, encoding="utf-8") as f:
        return set(json.load(f))


def salvar_estado(chaves: set[str]) -> None:
    with open(ESTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(chaves), f)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sheet-url", default=None, help="URL da planilha (padrao: planilha da SkinPet)")
    parser.add_argument("--pixel-id", default=None)
    parser.add_argument("--access-token", default=None)
    parser.add_argument("--test-event-code", default=None)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reenviar-tudo", action="store_true", help="Ignora o estado local e reenvia todas as linhas")
    args = parser.parse_args()

    pixel_id = args.pixel_id or os.environ.get("META_PIXEL_ID")
    access_token = args.access_token or os.environ.get("META_ACCESS_TOKEN")
    if not args.dry_run and not (pixel_id and access_token):
        sys.exit("Faltam credenciais: defina META_PIXEL_ID e META_ACCESS_TOKEN, ou rode com --dry-run.")

    if args.sheet_url:
        sheet_id, gid = transformador.sheet_id_and_gid_from_url(args.sheet_url)
    else:
        sheet_id, gid = transformador.DEFAULT_SHEET_ID, transformador.DEFAULT_GID

    print("Baixando planilha...", file=sys.stderr)
    conteudo_csv = transformador.baixar_planilha_csv(sheet_id, gid)
    reader = csv.DictReader(io.StringIO(conteudo_csv))

    todas_linhas = []
    for linha in reader:
        resultado = transformador.transformar_linha(linha)
        if resultado:
            todas_linhas.append(resultado)

    # Salva sempre o CSV completo e atualizado (o deliverable "CSV pra Meta").
    saida_csv = transformador.gerar_csv(todas_linhas)
    with open(CSV_SAIDA_PATH, "w", encoding="utf-8", newline="") as f:
        f.write(saida_csv)
    print(f"{len(todas_linhas)} linhas na planilha -> CSV atualizado em {CSV_SAIDA_PATH}", file=sys.stderr)

    estado = set() if args.reenviar_tudo else carregar_estado()
    agora = time.time()
    linhas_novas = []
    linhas_antigas_demais = 0
    chaves_novas = set()
    chaves_antigas = set()
    for linha in todas_linhas:
        chave = chave_linha(linha)
        if chave in estado:
            continue
        idade_segundos = agora - capi.iso_to_unix(linha["event_time"])
        if idade_segundos > JANELA_MAX_SEGUNDOS:
            # O Meta so aceita eventos server-side de ate 7 dias atras. Marca como
            # "visto" pra nao ficar tentando de novo a cada execucao, mas nao envia.
            linhas_antigas_demais += 1
            chaves_antigas.add(chave)
            continue
        linhas_novas.append(linha)
        chaves_novas.add(chave)

    if linhas_antigas_demais:
        print(f"{linhas_antigas_demais} linhas com mais de 7 dias -> Meta nao aceita via CAPI, nao serao enviadas (fora do funil automatico).", file=sys.stderr)
    print(f"{len(linhas_novas)} eventos novos (dentro da janela de 7 dias) desde a ultima execucao.", file=sys.stderr)

    if not linhas_novas:
        if chaves_antigas and not args.dry_run:
            salvar_estado(estado | chaves_antigas)
        return

    eventos = [capi.row_to_event(linha) for linha in linhas_novas]
    eventos = [e for e in eventos if e]

    if args.dry_run:
        print(json.dumps(eventos[:3], indent=2, ensure_ascii=False))
        print(f"... (mostrando ate 3 de {len(eventos)}, nada foi enviado, estado nao foi salvo)", file=sys.stderr)
        return

    total_success = 0
    total_error = 0
    for i in range(0, len(eventos), args.batch_size):
        lote = eventos[i:i + args.batch_size]
        resultado = capi.send_batch(pixel_id, access_token, lote, args.test_event_code)
        if "error" in resultado:
            total_error += len(lote)
            print(f"Lote {i // args.batch_size + 1}: ERRO -> {resultado['error'].get('error', resultado['error'])}", file=sys.stderr)
        else:
            recebidos = resultado.get("events_received", 0)
            total_success += recebidos
            print(f"Lote {i // args.batch_size + 1}: {recebidos} eventos recebidos.", file=sys.stderr)

    print(f"Resumo: {total_success} eventos aceitos, {total_error} com erro.", file=sys.stderr)

    # So marca como enviado o que nao deu erro generalizado (aqui, tudo-ou-nada por simplicidade:
    # se o lote inteiro falhou, essas linhas nao entram no estado e serao tentadas de novo na proxima).
    if total_error == 0:
        salvar_estado(estado | chaves_novas | chaves_antigas)
    else:
        print("Houve erro(s) de envio: estado NAO foi atualizado para as linhas com erro, serao tentadas novamente na proxima execucao.", file=sys.stderr)
        salvar_estado(estado | chaves_antigas)


if __name__ == "__main__":
    main()
