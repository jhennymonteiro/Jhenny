# Google Ads MCP

Servidor MCP que conecta o Claude diretamente a Google Ads API: permite
listar contas e campanhas, rodar relatorios (GAQL) e pausar/ativar
campanhas.

## 1. Conseguir o Developer Token

1. Acesse sua conta **Google Ads Manager (MCC)** (se voce nao tiver uma,
   crie em https://ads.google.com/home/tools/manager-accounts/).
2. Va em **Ferramentas e configuracoes > Configuracao > Centro de API**.
3. Solicite um **Developer Token**. O nivel "Test account" e liberado na
   hora e so funciona com contas de teste; para contas reais e preciso
   solicitar acesso "Basic" (aprovacao do Google, pode levar alguns dias).

## 2. Criar credenciais OAuth2 no Google Cloud

1. Va em https://console.cloud.google.com/ e crie um projeto (ou use um
   existente).
2. Ative a **Google Ads API** em "APIs e servicos > Biblioteca".
3. Em "APIs e servicos > Tela de consentimento OAuth", configure um app
   (pode ser "Externo" + modo de teste, adicionando seu proprio e-mail
   como usuario de teste).
4. Em "APIs e servicos > Credenciais", crie uma credencial do tipo
   **ID do cliente OAuth** (tipo "App para computador"). Anote o
   `client_id` e `client_secret`.

## 3. Gerar o Refresh Token

Com `client_id`/`client_secret` em maos, rode o script oficial da
biblioteca `google-ads` para gerar o refresh token (ele abre o navegador
para voce autorizar o acesso a conta de anuncios):

```bash
pip install google-ads
python3 -c "
from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_config(
    {
        'installed': {
            'client_id': 'SEU_CLIENT_ID',
            'client_secret': 'SEU_CLIENT_SECRET',
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
        }
    },
    scopes=['https://www.googleapis.com/auth/adwords'],
)
creds = flow.run_local_server(port=0)
print('refresh_token:', creds.refresh_token)
"
```

Guarde o `refresh_token` impresso.

## 4. Preencher google-ads.yaml

```bash
cd google_ads_mcp
cp google-ads.yaml.example google-ads.yaml
```

Edite `google-ads.yaml` com `developer_token`, `client_id`,
`client_secret`, `refresh_token` e, se voce acessa a conta de anuncios via
uma conta de gerente (MCC), o `login_customer_id` (ID da MCC, sem
hifens). **Nunca faca commit desse arquivo** — ele ja esta no
`.gitignore`.

## 5. Instalar dependencias e testar

```bash
pip install -r requirements.txt
export GOOGLE_ADS_CONFIGURATION_FILE_PATH="$(pwd)/google-ads.yaml"
python3 server.py
```

O servidor MCP conversa por stdio. O arquivo `.mcp.json` na raiz do repo
ja registra esse servidor como `google-ads` para o Claude Code — feche e
reabra a sessao apos preencher as credenciais para as ferramentas
aparecerem.

## Ferramentas disponiveis

O escopo OAuth usado (`adwords`) ja autoriza leitura e escrita completas na
conta — as ferramentas abaixo cobrem o ciclo de gerenciamento de ponta a
ponta. Qualquer relatorio ou listagem que nao esteja aqui pode ser feito com
`run_gaql_query`.

**Contas e relatorios**
- `list_accessible_customers()` — lista os IDs de contas acessiveis.
- `run_gaql_query(customer_id, query)` — roda qualquer query GAQL.

**Orcamentos**
- `list_campaign_budgets(customer_id)`
- `create_campaign_budget(customer_id, name, amount_micros)`
- `update_campaign_budget(customer_id, budget_id, amount_micros)`

**Campanhas**
- `list_campaigns(customer_id)`
- `get_campaign_performance(customer_id, last_n_days=7)`
- `create_search_campaign(customer_id, name, campaign_budget_resource_name, bidding_strategy, status)`
- `update_campaign(customer_id, campaign_id, name=None, status=None)`
- `set_campaign_status(customer_id, campaign_id, status)` — `ENABLED`,
  `PAUSED` ou `REMOVED` (equivale a excluir).

**Grupos de anuncios**
- `list_ad_groups(customer_id, campaign_id=None)`
- `create_ad_group(customer_id, campaign_id, name, cpc_bid_micros=None, status="ENABLED")`
- `update_ad_group(customer_id, ad_group_id, name=None, status=None, cpc_bid_micros=None)`

**Palavras-chave**
- `list_keywords(customer_id, ad_group_id=None)`
- `add_keywords(customer_id, ad_group_id, keywords, cpc_bid_micros=None)`
- `update_keyword_status(customer_id, criterion_resource_name, status)`

**Anuncios**
- `list_ads(customer_id, ad_group_id=None)`
- `create_responsive_search_ad(customer_id, ad_group_id, headlines, descriptions, final_urls)`
- `update_ad_status(customer_id, ad_group_ad_resource_name, status)`

Campanhas e anuncios novos sao criados como `PAUSED` por padrao — ative
manualmente (`set_campaign_status` / `update_ad_status`) depois de revisar.
