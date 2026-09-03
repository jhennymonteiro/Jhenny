#!/usr/bin/env python3
"""
Servidor MCP para a Google Ads API.

Expoe ferramentas de leitura (contas, campanhas, performance via GAQL) e uma
ferramenta de escrita (pausar/ativar campanha) para uso por assistentes MCP
como o Claude.

Configuracao:
  1. Copie google-ads.yaml.example para google-ads.yaml e preencha as
     credenciais (veja README.md para como obte-las).
  2. Defina a variavel de ambiente GOOGLE_ADS_CONFIGURATION_FILE_PATH
     apontando para esse arquivo, ou deixe o arquivo em
     ~/google-ads.yaml (caminho padrao da lib).

Uso local (stdio):
  python3 server.py
"""

import os
from typing import Any

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("google-ads")

API_VERSION = "v18"


def get_client() -> GoogleAdsClient:
    config_path = os.environ.get("GOOGLE_ADS_CONFIGURATION_FILE_PATH")
    if config_path:
        return GoogleAdsClient.load_from_storage(path=config_path, version=API_VERSION)
    return GoogleAdsClient.load_from_storage(version=API_VERSION)


def format_gads_error(exc: GoogleAdsException) -> str:
    lines = [f"Falha na requisicao (request-id: {exc.request_id}):"]
    for error in exc.failure.errors:
        lines.append(f"  - {error.error_code}: {error.message}")
    return "\n".join(lines)


@mcp.tool()
def list_accessible_customers() -> list[str]:
    """Lista os IDs de todas as contas do Google Ads acessiveis com as credenciais atuais."""
    client = get_client()
    service = client.get_service("CustomerService")
    try:
        response = service.list_accessible_customers()
    except GoogleAdsException as exc:
        return [format_gads_error(exc)]
    return [name.split("/")[-1] for name in response.resource_names]


@mcp.tool()
def run_gaql_query(customer_id: str, query: str) -> list[dict[str, Any]]:
    """
    Executa uma query GAQL (Google Ads Query Language) arbitraria e retorna as linhas como dicts.

    customer_id: ID da conta de anuncios, sem hifens (ex: "1234567890").
    query: query GAQL, ex:
      "SELECT campaign.id, campaign.name, campaign.status FROM campaign"
    """
    client = get_client()
    ga_service = client.get_service("GoogleAdsService")
    try:
        stream = ga_service.search_stream(customer_id=customer_id, query=query)
        rows = []
        for batch in stream:
            for row in batch.results:
                rows.append(client.json.to_dict(row))
        return rows
    except GoogleAdsException as exc:
        return [{"error": format_gads_error(exc)}]


@mcp.tool()
def list_campaigns(customer_id: str) -> list[dict[str, Any]]:
    """Lista campanhas (id, nome, status, tipo de canal) de uma conta."""
    query = """
        SELECT campaign.id, campaign.name, campaign.status,
               campaign.advertising_channel_type
        FROM campaign
        ORDER BY campaign.id
    """
    return run_gaql_query(customer_id, query)


@mcp.tool()
def get_campaign_performance(customer_id: str, last_n_days: int = 7) -> list[dict[str, Any]]:
    """Retorna metricas (cliques, impressoes, custo, conversoes) por campanha nos ultimos N dias."""
    query = f"""
        SELECT campaign.id, campaign.name,
               metrics.clicks, metrics.impressions,
               metrics.cost_micros, metrics.conversions
        FROM campaign
        WHERE segments.date DURING LAST_{last_n_days}_DAYS
        ORDER BY metrics.cost_micros DESC
    """
    return run_gaql_query(customer_id, query)


@mcp.tool()
def set_campaign_status(customer_id: str, campaign_id: str, status: str) -> str:
    """
    Ativa ou pausa uma campanha.

    status: "ENABLED" ou "PAUSED".
    """
    if status not in ("ENABLED", "PAUSED"):
        return "status invalido: use ENABLED ou PAUSED"

    client = get_client()
    campaign_service = client.get_service("CampaignService")
    operation = client.get_type("CampaignOperation")
    campaign = operation.update
    campaign.resource_name = campaign_service.campaign_path(customer_id, campaign_id)
    campaign.status = client.enums.CampaignStatusEnum[status]

    from google.protobuf import field_mask_pb2

    operation.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))

    try:
        response = campaign_service.mutate_campaigns(
            customer_id=customer_id, operations=[operation]
        )
    except GoogleAdsException as exc:
        return format_gads_error(exc)
    return f"Atualizado: {response.results[0].resource_name}"


if __name__ == "__main__":
    mcp.run()
