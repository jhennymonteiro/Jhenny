#!/usr/bin/env python3
"""
Servidor MCP para a Google Ads API.

Cobre o ciclo de gerenciamento da conta: orcamentos, campanhas, grupos de
anuncios, palavras-chave e anuncios (ler, criar, editar, pausar/ativar,
remover), alem de relatorios via GAQL livre.

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

from google.api_core import protobuf_helpers
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


def run_mutate(mutate_fn, customer_id: str, operations: list) -> str:
    try:
        response = mutate_fn(customer_id=customer_id, operations=operations)
    except GoogleAdsException as exc:
        return format_gads_error(exc)
    return ", ".join(r.resource_name for r in response.results)


# ---------------------------------------------------------------------------
# Contas e relatorios
# ---------------------------------------------------------------------------


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
    Use isso para qualquer relatorio ou listagem que as outras ferramentas nao cobrem.

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


# ---------------------------------------------------------------------------
# Orcamentos
# ---------------------------------------------------------------------------


@mcp.tool()
def list_campaign_budgets(customer_id: str) -> list[dict[str, Any]]:
    """Lista os orcamentos de campanha (id, nome, valor diario em micros) da conta."""
    query = """
        SELECT campaign_budget.id, campaign_budget.name,
               campaign_budget.amount_micros, campaign_budget.delivery_method
        FROM campaign_budget
        ORDER BY campaign_budget.id
    """
    return run_gaql_query(customer_id, query)


@mcp.tool()
def create_campaign_budget(customer_id: str, name: str, amount_micros: int) -> str:
    """
    Cria um orcamento de campanha (diario). 1 unidade monetaria = 1_000_000 micros
    (ex: R$ 50/dia = 50_000_000).
    Retorna o resource_name do orcamento, para usar em create_search_campaign.
    """
    client = get_client()
    service = client.get_service("CampaignBudgetService")
    operation = client.get_type("CampaignBudgetOperation")
    budget = operation.create
    budget.name = name
    budget.amount_micros = amount_micros
    budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
    return run_mutate(service.mutate_campaign_budgets, customer_id, [operation])


@mcp.tool()
def update_campaign_budget(customer_id: str, budget_id: str, amount_micros: int) -> str:
    """Atualiza o valor diario (em micros) de um orcamento existente."""
    client = get_client()
    service = client.get_service("CampaignBudgetService")
    operation = client.get_type("CampaignBudgetOperation")
    budget = operation.update
    budget.resource_name = service.campaign_budget_path(customer_id, budget_id)
    budget.amount_micros = amount_micros
    operation.update_mask.CopyFrom(protobuf_helpers.field_mask(None, budget._pb))
    return run_mutate(service.mutate_campaign_budgets, customer_id, [operation])


# ---------------------------------------------------------------------------
# Campanhas
# ---------------------------------------------------------------------------


@mcp.tool()
def list_campaigns(customer_id: str) -> list[dict[str, Any]]:
    """Lista campanhas (id, nome, status, tipo de canal, orcamento) de uma conta."""
    query = """
        SELECT campaign.id, campaign.name, campaign.status,
               campaign.advertising_channel_type, campaign.campaign_budget
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
def create_search_campaign(
    customer_id: str,
    name: str,
    campaign_budget_resource_name: str,
    bidding_strategy: str = "MAXIMIZE_CONVERSIONS",
    status: str = "PAUSED",
) -> str:
    """
    Cria uma campanha de Pesquisa (Search). Comeca PAUSED por seguranca, salvo
    indicacao em contrario.

    bidding_strategy: "MAXIMIZE_CONVERSIONS" ou "MANUAL_CPC".
    status: "ENABLED" ou "PAUSED".
    """
    client = get_client()
    service = client.get_service("CampaignService")
    operation = client.get_type("CampaignOperation")
    campaign = operation.create
    campaign.name = name
    campaign.campaign_budget = campaign_budget_resource_name
    campaign.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum.SEARCH
    campaign.status = client.enums.CampaignStatusEnum[status]
    campaign.network_settings.target_google_search = True
    campaign.network_settings.target_search_network = True
    campaign.network_settings.target_content_network = False
    campaign.network_settings.target_partner_search_network = False

    if bidding_strategy == "MANUAL_CPC":
        campaign.manual_cpc.enhanced_cpc_enabled = False
    else:
        campaign.maximize_conversions.CopyFrom(client.get_type("MaximizeConversions")())

    return run_mutate(service.mutate_campaigns, customer_id, [operation])


@mcp.tool()
def update_campaign(
    customer_id: str,
    campaign_id: str,
    name: str | None = None,
    status: str | None = None,
) -> str:
    """
    Atualiza campos de uma campanha existente. Passe apenas os campos que
    quer alterar (deixe os demais como None).

    status: "ENABLED", "PAUSED" ou "REMOVED" (equivale a excluir a campanha).
    """
    client = get_client()
    service = client.get_service("CampaignService")
    operation = client.get_type("CampaignOperation")
    campaign = operation.update
    campaign.resource_name = service.campaign_path(customer_id, campaign_id)
    if name is not None:
        campaign.name = name
    if status is not None:
        campaign.status = client.enums.CampaignStatusEnum[status]
    operation.update_mask.CopyFrom(protobuf_helpers.field_mask(None, campaign._pb))
    return run_mutate(service.mutate_campaigns, customer_id, [operation])


@mcp.tool()
def set_campaign_status(customer_id: str, campaign_id: str, status: str) -> str:
    """Ativa, pausa ou remove uma campanha. status: "ENABLED", "PAUSED" ou "REMOVED"."""
    return update_campaign(customer_id, campaign_id, status=status)


# ---------------------------------------------------------------------------
# Grupos de anuncios
# ---------------------------------------------------------------------------


@mcp.tool()
def list_ad_groups(customer_id: str, campaign_id: str | None = None) -> list[dict[str, Any]]:
    """Lista grupos de anuncios, opcionalmente filtrando por campanha."""
    where = f"WHERE campaign.id = {campaign_id}" if campaign_id else ""
    query = f"""
        SELECT ad_group.id, ad_group.name, ad_group.status,
               ad_group.campaign, ad_group.cpc_bid_micros
        FROM ad_group
        {where}
        ORDER BY ad_group.id
    """
    return run_gaql_query(customer_id, query)


@mcp.tool()
def create_ad_group(
    customer_id: str,
    campaign_id: str,
    name: str,
    cpc_bid_micros: int | None = None,
    status: str = "ENABLED",
) -> str:
    """Cria um grupo de anuncios dentro de uma campanha."""
    client = get_client()
    ad_group_service = client.get_service("AdGroupService")
    campaign_service = client.get_service("CampaignService")
    operation = client.get_type("AdGroupOperation")
    ad_group = operation.create
    ad_group.name = name
    ad_group.campaign = campaign_service.campaign_path(customer_id, campaign_id)
    ad_group.status = client.enums.AdGroupStatusEnum[status]
    ad_group.type_ = client.enums.AdGroupTypeEnum.SEARCH_STANDARD
    if cpc_bid_micros is not None:
        ad_group.cpc_bid_micros = cpc_bid_micros
    return run_mutate(ad_group_service.mutate_ad_groups, customer_id, [operation])


@mcp.tool()
def update_ad_group(
    customer_id: str,
    ad_group_id: str,
    name: str | None = None,
    status: str | None = None,
    cpc_bid_micros: int | None = None,
) -> str:
    """
    Atualiza um grupo de anuncios. Passe apenas os campos que quer alterar.
    status: "ENABLED", "PAUSED" ou "REMOVED".
    """
    client = get_client()
    service = client.get_service("AdGroupService")
    operation = client.get_type("AdGroupOperation")
    ad_group = operation.update
    ad_group.resource_name = service.ad_group_path(customer_id, ad_group_id)
    if name is not None:
        ad_group.name = name
    if status is not None:
        ad_group.status = client.enums.AdGroupStatusEnum[status]
    if cpc_bid_micros is not None:
        ad_group.cpc_bid_micros = cpc_bid_micros
    operation.update_mask.CopyFrom(protobuf_helpers.field_mask(None, ad_group._pb))
    return run_mutate(service.mutate_ad_groups, customer_id, [operation])


# ---------------------------------------------------------------------------
# Palavras-chave
# ---------------------------------------------------------------------------


@mcp.tool()
def list_keywords(customer_id: str, ad_group_id: str | None = None) -> list[dict[str, Any]]:
    """Lista palavras-chave (criterios de pesquisa), opcionalmente filtrando por grupo de anuncios."""
    where = f"WHERE ad_group.id = {ad_group_id}" if ad_group_id else ""
    query = f"""
        SELECT ad_group_criterion.criterion_id, ad_group_criterion.keyword.text,
               ad_group_criterion.keyword.match_type, ad_group_criterion.status,
               ad_group_criterion.ad_group
        FROM ad_group_criterion
        WHERE ad_group_criterion.type = KEYWORD {"AND " + where[6:] if where else ""}
        ORDER BY ad_group_criterion.criterion_id
    """
    return run_gaql_query(customer_id, query)


@mcp.tool()
def add_keywords(
    customer_id: str,
    ad_group_id: str,
    keywords: list[dict[str, str]],
    cpc_bid_micros: int | None = None,
) -> str:
    """
    Adiciona palavras-chave a um grupo de anuncios.

    keywords: lista de dicts {"text": "tenis de corrida", "match_type": "PHRASE"}.
    match_type: "EXACT", "PHRASE" ou "BROAD".
    """
    client = get_client()
    ad_group_service = client.get_service("AdGroupService")
    criterion_service = client.get_service("AdGroupCriterionService")
    operations = []
    for kw in keywords:
        operation = client.get_type("AdGroupCriterionOperation")
        criterion = operation.create
        criterion.ad_group = ad_group_service.ad_group_path(customer_id, ad_group_id)
        criterion.status = client.enums.AdGroupCriterionStatusEnum.ENABLED
        criterion.keyword.text = kw["text"]
        criterion.keyword.match_type = client.enums.KeywordMatchTypeEnum[kw.get("match_type", "BROAD")]
        if cpc_bid_micros is not None:
            criterion.cpc_bid_micros = cpc_bid_micros
        operations.append(operation)
    return run_mutate(criterion_service.mutate_ad_group_criteria, customer_id, operations)


@mcp.tool()
def update_keyword_status(customer_id: str, criterion_resource_name: str, status: str) -> str:
    """
    Ativa, pausa ou remove uma palavra-chave.

    criterion_resource_name: valor de ad_group_criterion.resource_name (obtido via list_keywords,
      formato "customers/{id}/adGroupCriteria/{ad_group_id}~{criterion_id}").
    status: "ENABLED", "PAUSED" ou "REMOVED".
    """
    client = get_client()
    service = client.get_service("AdGroupCriterionService")
    operation = client.get_type("AdGroupCriterionOperation")
    criterion = operation.update
    criterion.resource_name = criterion_resource_name
    criterion.status = client.enums.AdGroupCriterionStatusEnum[status]
    operation.update_mask.CopyFrom(protobuf_helpers.field_mask(None, criterion._pb))
    return run_mutate(service.mutate_ad_group_criteria, customer_id, [operation])


# ---------------------------------------------------------------------------
# Anuncios
# ---------------------------------------------------------------------------


@mcp.tool()
def list_ads(customer_id: str, ad_group_id: str | None = None) -> list[dict[str, Any]]:
    """Lista anuncios, opcionalmente filtrando por grupo de anuncios."""
    where = f"WHERE ad_group.id = {ad_group_id}" if ad_group_id else ""
    query = f"""
        SELECT ad_group_ad.ad.id, ad_group_ad.status, ad_group_ad.ad.type,
               ad_group_ad.ad.final_urls, ad_group_ad.ad_group
        FROM ad_group_ad
        {where}
        ORDER BY ad_group_ad.ad.id
    """
    return run_gaql_query(customer_id, query)


@mcp.tool()
def create_responsive_search_ad(
    customer_id: str,
    ad_group_id: str,
    headlines: list[str],
    descriptions: list[str],
    final_urls: list[str],
) -> str:
    """
    Cria um anuncio de pesquisa responsivo (RSA).

    headlines: 3 a 15 titulos, ate 30 caracteres cada.
    descriptions: 2 a 4 descricoes, ate 90 caracteres cada.
    final_urls: URL(s) de destino do anuncio.
    """
    client = get_client()
    ad_group_service = client.get_service("AdGroupService")
    ad_group_ad_service = client.get_service("AdGroupAdService")
    operation = client.get_type("AdGroupAdOperation")
    ad_group_ad = operation.create
    ad_group_ad.ad_group = ad_group_service.ad_group_path(customer_id, ad_group_id)
    ad_group_ad.status = client.enums.AdGroupAdStatusEnum.PAUSED

    ad = ad_group_ad.ad
    ad.final_urls.extend(final_urls)
    for text in headlines:
        asset = client.get_type("AdTextAsset")
        asset.text = text
        ad.responsive_search_ad.headlines.append(asset)
    for text in descriptions:
        asset = client.get_type("AdTextAsset")
        asset.text = text
        ad.responsive_search_ad.descriptions.append(asset)

    return run_mutate(ad_group_ad_service.mutate_ad_group_ads, customer_id, [operation])


@mcp.tool()
def update_ad_status(customer_id: str, ad_group_ad_resource_name: str, status: str) -> str:
    """
    Ativa, pausa ou remove um anuncio.

    ad_group_ad_resource_name: obtido via list_ads (campo ad_group_ad.resource_name).
    status: "ENABLED", "PAUSED" ou "REMOVED".
    """
    client = get_client()
    service = client.get_service("AdGroupAdService")
    operation = client.get_type("AdGroupAdOperation")
    ad_group_ad = operation.update
    ad_group_ad.resource_name = ad_group_ad_resource_name
    ad_group_ad.status = client.enums.AdGroupAdStatusEnum[status]
    operation.update_mask.CopyFrom(protobuf_helpers.field_mask(None, ad_group_ad._pb))
    return run_mutate(service.mutate_ad_group_ads, customer_id, [operation])


if __name__ == "__main__":
    mcp.run()
