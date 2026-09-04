# Automação WhatsApp → Meta CAPI — Obralar

## Objetivo
Detectar quando um pedido é confirmado no WhatsApp da Obralar e enviar automaticamente um evento de conversão (`Purchase`) para o Pixel da Meta via **Conversions API (CAPI)**, usando **Evolution API** (WhatsApp) + **Make** (automação) + **Graph API** (Meta).

## Visão geral do fluxo

```
WhatsApp (Evolution API)
        │  webhook (MESSAGES_UPSERT)
        ▼
   Make — Custom Webhook
        │  filtro: mensagem do vendedor contendo
        │  "Seu pedido foi confirmado"
        ▼
   Set Variable (calcula hash do telefone)
        │
        ▼
   HTTP POST → graph.facebook.com/{pixel_id}/events
        │
        ▼
   Meta Pixel Obralar (evento Purchase)
```

## Componentes

| Item | Valor |
|---|---|
| Cenário Make | `WhatsApp Obralar -> Meta CAPI (Purchase)` (id `6144598`) |
| Webhook Make | `https://hook.us2.make.com/1j37c3avgdwmdfyr88q8mffphgh3rc3g` |
| Pixel Meta (dataset) | `Obralar` — id `1170799445087648` |
| Negócio Meta | Obralar - Ceilandia — business id `687689826711087` |
| Evento enviado | `Purchase` |
| Fonte da ação | `chat` |

## Gatilho de detecção

A automação **não** usa palavras-chave do cliente (ex.: "comprei", "paguei", "R$") como gatilho principal — mensagens de clientes perguntando preços geravam falsos positivos.

O gatilho real é a **mensagem de confirmação que o vendedor envia** ao fechar o pedido:

> "Seu pedido foi confirmado, sua entrega chegará em breve! 🚚🤩 Obrigado pela confiança!"

Condição no Make: `fromMe = true` **E** o texto da mensagem contém `"Seu pedido foi confirmado"`.

## Dados enviados ao Meta

```json
{
  "data": [{
    "event_name": "Purchase",
    "event_time": <timestamp da mensagem>,
    "event_id": <id da mensagem, evita duplicidade>,
    "action_source": "chat",
    "user_data": {
      "ph": ["<telefone do cliente em SHA-256>"]
    },
    "custom_data": {
      "currency": "BRL",
      "value": 0,
      "content_name": "Pedido confirmado"
    }
  }],
  "access_token": "<token da Conversions API, gerado no Events Manager>"
}
```

### Sobre o valor da compra
Por decisão do negócio, o evento é enviado **sem o valor real da venda** (`value: 0`) — a conversa envolve preços de vários itens fragmentados, o que tornaria a extração automática do valor pouco confiável. O evento serve para sinalizar a conversão ao algoritmo de otimização de anúncios; o valor real de cada venda deve ser acompanhado separadamente (planilha/CRM), sem realimentar o Meta (o Meta não permite editar um evento já enviado).

### Por que `currency` é obrigatório mesmo sem valor
O Meta rejeita eventos `Purchase` sem o campo `currency`, mesmo quando `value` não é informado ou é `0`. Erro retornado quando ausente:
```
error_subcode: 2804010 — "Moeda ausente para o evento de compra"
```

## Envio em tempo real (não em lote)
O cenário dispara **apenas quando uma mensagem qualificada chega** (arquitetura orientada a webhook, sem varredura periódica) — enviar em lote (ex.: 1x por semana) não economizaria operações do Make, e prejudicaria a otimização da campanha, já que o Meta usa o sinal de conversão em tempo real para ajustar a entrega dos anúncios.

## Problemas encontrados e corrigidos durante a implementação

1. **Webhook antigo quebrado** — o webhook original (`Recebe a Mensagem`, id `2770239`) tinha uma configuração de autenticação incompleta (exigia uma API key que nunca foi cadastrada), o que impedia a inicialização do cenário (`BlueprintValidationError`). Foi substituído por um webhook novo e limpo (`Recebe Mensagem - Obralar`, id `2773186`).
2. **Operador de filtro incorreto** — o filtro usava `text:contains` (com "s"), que não é um operador válido no Make. O correto é `text:contain`.
3. **Campo obrigatório ausente no módulo HTTP** — faltava `followAllRedirects` (booleano, aninhado em "Seguir redirecionamento"), causando `BundleValidationError` silencioso.
4. **Funções aninhadas quebrando o JSON** — calcular `sha256(substring(...))` diretamente dentro da string JSON escapada do corpo da requisição corrompia o payload (Meta retornava `"(#100) The parameter data is required"`). Resolvido pré-calculando o hash do telefone num módulo separado (`Set variable`) antes de montar o corpo da requisição.
5. **Moeda ausente** — ver seção acima.

## Limitações conhecidas

- **Sem valor de venda real** — decisão consciente, ver seção "Sobre o valor da compra".
- **Sem atribuição de campanha (`ctwa_clid`)** — diferente do Skinpet, essa automação não tenta capturar o clique do anúncio que originou a conversa. Pode ser adicionada seguindo o mesmo padrão implementado no cenário do Skinpet (ver documentação correspondente), assim que fizer sentido para o negócio.
- **Duplicidade** — se o vendedor reenviar a mensagem de confirmação para o mesmo pedido, um novo evento `Purchase` será disparado (não há trava de duplicidade implementada, por decisão de simplicidade).

## Como testar

Envie um POST simulando o payload da Evolution API diretamente para a URL do webhook:

```bash
curl -X POST https://hook.us2.make.com/1j37c3avgdwmdfyr88q8mffphgh3rc3g \
  -H "Content-Type: application/json" \
  -d '{
    "data": {
      "key": { "remoteJid": "55XXXXXXXXXXX@s.whatsapp.net", "fromMe": true, "id": "TESTE-001" },
      "message": { "conversation": "Seu pedido foi confirmado, sua entrega chegará em breve! 🚚🤩\nObrigado pela confiança!" },
      "messageTimestamp": 1735689600
    }
  }'
```

Confirme no histórico de execuções do Make e/ou no Events Manager (Pixel Obralar → Test Events, se um `test_event_code` for adicionado) que o evento chegou.

## Status
✅ Testado e confirmado — resposta do Meta: `200 OK`, `events_received: 1`.
