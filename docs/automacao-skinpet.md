# Automação WhatsApp → Meta CAPI — Skinpet (Dra Priscila Alves)

## Objetivo
Detectar quando um agendamento de consulta é confirmado no WhatsApp da Skinpet e enviar automaticamente um evento de conversão (`LeadSubmitted`) para o Pixel da Meta via **Conversions API (CAPI)**, usando **Evolution API** (WhatsApp) + **Make** (automação) + **Graph API** (Meta). Inclui também a estrutura (pronta, mas hoje sem dado real) para atribuir a conversão ao clique do anúncio que originou a conversa (`ctwa_clid`).

## Visão geral do fluxo

```
WhatsApp (Evolution API)
        │  webhook (MESSAGES_UPSERT)
        ▼
   Make — Custom Webhook
        │
        ├── Rota A: mensagem recebida do cliente com ctwa_clid
        │    → guarda o ctwa_clid num Data Store, indexado pelo telefone
        │
        └── Rota B: mensagem da clínica com o texto de orientações
             → busca o ctwa_clid salvo (se houver)
             → calcula hash do telefone
             → HTTP POST → graph.facebook.com/{pixel_id}/events
             → Meta Pixel Skinpet (evento LeadSubmitted)
```

## Componentes

| Item | Valor |
|---|---|
| Cenário Make | `WhatsApp Skinpet -> Meta CAPI (LeadSubmitted)` (id `6145479`) |
| Webhook Make | `https://hook.us2.make.com/15lckr4x03bfd8efhqfu9ko5qz5to7ct` |
| Pixel Meta (dataset) | `Pixel Skinpet` — id `2355495018320412` |
| Negócio Meta | Dra Priscila Alves — business id `245116386254492` |
| Conta de anúncios | DPA Anúncios — id `512219180511491` |
| Página do Facebook (WhatsApp Business) | Dra Priscila Alves Dermatologia Veterinária — id `1435974993319854` |
| Data Store (captura de `ctwa_clid`) | `CTWA ClickID - Skinpet` (id `143786`) |
| Evento enviado | `LeadSubmitted` |
| Fonte da ação | `chat` *(ver seção sobre `business_messaging` abaixo)* |

## Gatilho de detecção

O gatilho é a **mensagem padrão de orientações que a clínica envia** ao confirmar um agendamento:

> "Seguem as orientações de atendimento: • Levar todas as receitas de tratamentos anteriores... 🚨Por favor, NÃO SE ATRASE..."

Condição no Make: `fromMe = true` **E** o texto da mensagem contém `"Seguem as orientações de atendimento"`.

## Dados enviados ao Meta

```json
{
  "data": [{
    "event_name": "LeadSubmitted",
    "event_time": <timestamp da mensagem>,
    "event_id": <id da mensagem>,
    "action_source": "chat",
    "user_data": {
      "ph": ["<telefone do paciente em SHA-256>"]
    },
    "custom_data": {
      "content_name": "Consulta agendada"
    }
  }],
  "access_token": "<token da Conversions API, gerado no Events Manager>"
}
```

## Atribuição de campanha (`ctwa_clid`) — implementado, mas pendente de dado real

### O que é
Quando alguém clica num anúncio "Clique para o WhatsApp" (Click-to-WhatsApp), a primeira mensagem que ela envia carrega um identificador (`ctwa_clid`) que permite ao Meta ligar aquela conversa ao anúncio/campanha específico. Enviando esse identificador junto ao evento de conversão, o Meta consegue atribuir a venda/lead à campanha certa (e otimizar a entrega dos anúncios com base nisso).

### Como a automação captura isso
- **Rota A** do cenário roda em toda mensagem recebida do paciente (`fromMe: false`). Se o payload da Evolution API trouxer `data.adReferral.ctwaClid` ou `data.contextInfo.ctwaClid`, o valor é salvo no Data Store `CTWA ClickID - Skinpet`, indexado pelo telefone do contato.
- **Rota B** (a do gatilho de confirmação) busca esse valor salvo antes de montar o evento.

### Por que hoje o evento vai sem `ctwa_clid`
Existe um **bug conhecido na Evolution API** (confirmado na versão 2.3.7, a mesma em uso): o campo `ctwa_clid` é descartado antes de chegar ao webhook `messages.upsert`, mesmo quando o WhatsApp o envia. Ver: [issue #2645](https://github.com/evolution-foundation/evolution-api/issues/2645) (aberta, sem correção oficial até o momento).

Por isso, o cenário está configurado com `action_source: "chat"` (não exige `ctwa_clid`) em vez de `action_source: "business_messaging"` (que **exige obrigatoriamente** `ctwa_clid` e `page_id` — testado e confirmado: sem eles, o Meta rejeita o evento com erro 400).

### Como ativar a atribuição completa quando o bug for corrigido
1. Confirmar que a Evolution API foi atualizada para uma versão que já inclui o `ctwa_clid` no payload do webhook (checar a issue linkada acima).
2. No módulo HTTP do cenário, trocar:
   - `"action_source": "chat"` → `"action_source": "business_messaging"`
   - adicionar `"messaging_channel": "whatsapp"` no corpo do evento
   - adicionar `"page_id": "1435974993319854"` dentro de `user_data`
   - adicionar `"ctwa_clid": "<valor buscado no Data Store>"` dentro de `user_data`
3. Essa configuração já foi testada isoladamente com um valor de `ctwa_clid` simulado e retornou `200 OK` do Meta — a estrutura está validada, só falta o dado real chegar da Evolution API.

## Problemas encontrados e corrigidos durante a implementação

1. **Operador de filtro incorreto** — `text:contains` (inválido) → `text:contain` (correto).
2. **Campo obrigatório ausente no módulo HTTP** — faltava `followAllRedirects`.
3. **Funções aninhadas quebrando o JSON** — `sha256(substring(...))` direto dentro do corpo da requisição corrompia o payload. Resolvido pré-calculando o hash num módulo `Set variable` separado.
4. **Erro de módulo "Get a record"** — quando o telefone ainda não tinha `ctwa_clid` salvo, o módulo de busca (`Get a record`) lançava erro e travava o fluxo. Resolvido com um tratamento de erro (`Resume`) que substitui por um valor vazio e permite o fluxo continuar.
5. **`business_messaging` exige `page_id` e `ctwa_clid`** — ver seção acima.

## Limitações conhecidas

- **Sem valor de consulta** — o evento não carrega valor monetário (é um agendamento, não uma venda com preço fixo).
- **Atribuição de campanha inativa** — ver seção "Atribuição de campanha" acima.
- **Duplicidade** — se a clínica reenviar a mensagem de orientações para o mesmo contato, um novo evento será disparado.

## Como testar

```bash
curl -X POST https://hook.us2.make.com/15lckr4x03bfd8efhqfu9ko5qz5to7ct \
  -H "Content-Type: application/json" \
  -d '{
    "data": {
      "key": { "remoteJid": "55XXXXXXXXXXX@s.whatsapp.net", "fromMe": true, "id": "TESTE-001" },
      "message": { "conversation": "Seguem as orientações de atendimento: ..." },
      "messageTimestamp": 1735689600
    }
  }'
```

## Status
✅ Testado e confirmado — resposta do Meta: `200 OK`, `events_received: 1`.
