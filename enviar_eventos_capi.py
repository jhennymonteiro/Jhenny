#!/usr/bin/env python3
"""
Envia os leads/vendas da SkinPet para o Meta via Conversions API (CAPI).

Uso:
  export META_ACCESS_TOKEN="seu_token_do_dataset/pixel"
  export META_PIXEL_ID="123456789012345"
  python3 enviar_eventos_capi.py Controle_de_Leads_-_SkinPet_adaptado.csv

Opcional:
  --test-event-code TEST12345   # para conferir no Test Events do Events Manager antes de valer
  --dry-run                     # so mostra o payload, nao envia nada
  --batch-size 50                # eventos por requisicao (padrao 50)
"""

import argparse
import csv
import hashlib
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

GRAPH_API_VERSION = "v21.0"


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def normalize_phone(*candidates: str) -> str | None:
    for raw in candidates:
        digits = "".join(ch for ch in (raw or "") if ch.isdigit())
        if digits:
            return digits
    return None


def iso_to_unix(event_time: str) -> int:
    dt = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
    return int(dt.astimezone(timezone.utc).timestamp())


def row_to_event(row: dict) -> dict | None:
    event_name = (row.get("event_name") or "").strip()
    event_time = (row.get("event_time") or "").strip()
    if not event_name or not event_time:
        return None

    user_data = {}

    email = next((row[c] for c in ("email", "email.1", "email.2") if row.get(c)), None)
    if email:
        user_data["em"] = [sha256_hex(email)]

    phone = normalize_phone(row.get("phone.2"), row.get("phone"), row.get("phone.1"))
    if phone:
        user_data["ph"] = [sha256_hex(phone)]

    if row.get("fn"):
        user_data["fn"] = [sha256_hex(row["fn"])]
    if row.get("ln"):
        user_data["ln"] = [sha256_hex(row["ln"])]
    if row.get("country"):
        user_data["country"] = [sha256_hex(row["country"])]

    if not user_data:
        return None

    event = {
        "event_name": event_name,
        "event_time": iso_to_unix(event_time),
        "action_source": "other",
        "user_data": user_data,
    }

    value = (row.get("value") or "").strip()
    currency = (row.get("currency") or "").strip()
    if value and currency:
        event["custom_data"] = {"value": float(value), "currency": currency}

    return event


def load_events(csv_path: str) -> list[dict]:
    events = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # colunas de email/phone repetidas -> pandas-style suffixes .1/.2
        fieldnames = reader.fieldnames or []
        seen = {}
        renamed = []
        for name in fieldnames:
            seen[name] = seen.get(name, -1) + 1
            renamed.append(name if seen[name] == 0 else f"{name}.{seen[name]}")
        reader.fieldnames = renamed

        for raw_row in reader:
            event = row_to_event(raw_row)
            if event:
                events.append(event)
    return events


def send_batch(pixel_id: str, access_token: str, batch: list[dict], test_event_code: str | None) -> dict:
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{pixel_id}/events"
    payload = {"data": batch, "access_token": access_token}
    if test_event_code:
        payload["test_event_code"] = test_event_code

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": json.loads(e.read().decode("utf-8"))}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path", help="CSV adaptado (formato Meta customer file)")
    parser.add_argument("--pixel-id", default=None, help="Sobrescreve META_PIXEL_ID")
    parser.add_argument("--access-token", default=None, help="Sobrescreve META_ACCESS_TOKEN")
    parser.add_argument("--test-event-code", default=None)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import os

    pixel_id = args.pixel_id or os.environ.get("META_PIXEL_ID")
    access_token = args.access_token or os.environ.get("META_ACCESS_TOKEN")

    if not args.dry_run and not (pixel_id and access_token):
        sys.exit(
            "Faltam credenciais: defina META_PIXEL_ID e META_ACCESS_TOKEN (ou use --pixel-id/--access-token), "
            "ou rode com --dry-run para so ver o payload."
        )

    events = load_events(args.csv_path)
    print(f"{len(events)} eventos prontos para envio.")

    if args.dry_run:
        print(json.dumps(events[:2], indent=2, ensure_ascii=False))
        print(f"... (mostrando 2 de {len(events)})")
        return

    total_success = 0
    total_error = 0
    for i in range(0, len(events), args.batch_size):
        batch = events[i : i + args.batch_size]
        result = send_batch(pixel_id, access_token, batch, args.test_event_code)
        if "error" in result:
            total_error += len(batch)
            print(f"Lote {i // args.batch_size + 1}: ERRO -> {result['error'].get('error', result['error'])}")
        else:
            received = result.get("events_received", 0)
            total_success += received
            print(f"Lote {i // args.batch_size + 1}: {received} eventos recebidos. fbtrace_id={result.get('fbtrace_id')}")
        time.sleep(0.3)

    print(f"\nResumo: {total_success} eventos aceitos, {total_error} eventos com erro.")


if __name__ == "__main__":
    main()
