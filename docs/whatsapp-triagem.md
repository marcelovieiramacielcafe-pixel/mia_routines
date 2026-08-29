# Triagem de WhatsApp — instalação

Guia para o Marcelo. Não é preciso perceber de programação: são comandos para
copiar e colar, por ordem. Onde disser **[Marcelo]**, é uma coisa que só tu
podes fazer.

A Mia lê as notificações do WhatsApp e avisa-te. **Nunca responde a ninguém**,
nem com um «ok». Não há aqui nenhum cliente de WhatsApp — só um serviço que
recebe notificações reencaminhadas do teu telemóvel.

---

## Antes de tudo: dez minutos de bancada

Há uma pergunta que decide a configuração e que só se responde com o telemóvel
na mão: **quando silencias uma conversa dentro do WhatsApp, ela continua a
gerar notificação?**

Faz a Parte 1 (telemóvel) apontando a app a `https://mia-aux.duckdns.org/saude`
só para confirmar que envia, e depois pede a alguém que te mande uma mensagem
em cada uma destas condições, vendo o que aparece no registo do VPS:

| # | Conversa configurada assim | O que aprendes |
|---|---|---|
| 1 | Normal, sem silêncio | A captura funciona de todo |
| 2 | **Silenciada dentro do WhatsApp** | **Decide tudo.** Se chegar, não mexes em mais nada |
| 3 | Sem silêncio no WhatsApp + conversa em «Silencioso» no Android | Telemóvel calado, notificação capturada |
| 4 | Telemóvel em «Não incomodar» | Se a captura sobrevive à noite |

Se o teste 2 for positivo, não tens de tirar o silêncio a grupo nenhum e o
telemóvel fica tão calado como está hoje.

O «Silencioso» por conversa (teste 3) faz-se assim: carrega **longamente** na
notificação da conversa → **Silencioso**. Precisa de Android 11 ou superior.
Não mexas nas definições de canal da app inteira: isso silenciaria também os
grupos de trabalho onde queres que o telemóvel apite.

---

## Parte 1 — telemóvel Android

1. Instala o **NotificationForwarder**
   (<https://github.com/ItsAzni/NotificationForwarder>, licença MIT).
2. Dá-lhe a permissão de **acesso às notificações** quando pedir. É a permissão
   que lhe deixa ler o que aparece na gaveta.
3. Configura o destino:
   - **URL:** `https://mia-aux.duckdns.org/notificacao`
   - **Método:** `POST`
   - **Cabeçalho:** `Authorization` com o valor `Bearer SEGREDO`, substituindo
     `SEGREDO` pelo que geraste no passo 2 da Parte 2.
   - **Corpo:**
     ```json
     {"app": "{packageName}", "conversa": "{title}", "texto": "{text}"}
     ```
4. **Filtra só o WhatsApp** na lista de apps da própria aplicação. O serviço
   descarta tudo o que não seja WhatsApp, mas é melhor não sair do telemóvel.
5. **Importante:** Definições do Android → Bateria → procura a app e põe-na em
   **«Sem restrições»**. Se não fizeres isto, o Android mata-a ao fim de umas
   horas e deixas de receber avisos — sem te dizer nada.

---

## Parte 2 — VPS

```bash
# 1. Código
cd /opt && git clone https://github.com/marcelovieiramacielcafe-pixel/mia_routines.git
cd mia_routines && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2. Segredo de ingestão — guarda o que isto imprime, precisas dele no telemóvel
.venv/bin/python -c "import secrets;print(secrets.token_urlsafe(32))"

# 3. Configuração
cp .env.example .env && nano .env
```

No `.env` preenche: `TRIAGEM_INGEST_TOKEN` (o segredo do passo 2),
`TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` (**[Marcelo]** — o token novo do
@BotFather, não o antigo), `ANTHROPIC_API_KEY`, e a lista
`TRIAGEM_URGENTE_CONVERSAS` com os nomes tal como aparecem no teu WhatsApp.

```bash
# 4. Fecha o ficheiro a toda a gente menos ao dono
chmod 600 .env
```

### Serviço que arranca sozinho

```bash
sudo tee /etc/systemd/system/triagem.service >/dev/null <<'EOF'
[Unit]
Description=Triagem de WhatsApp da Mia
After=network.target

[Service]
WorkingDirectory=/opt/mia_routines
ExecStart=/opt/mia_routines/.venv/bin/python main_triage.py servir
Restart=always
RestartSec=5
User=mia

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload && sudo systemctl enable --now triagem
```

### Caddy (TLS)

O serviço escuta só em `127.0.0.1`. Acrescenta ao `Caddyfile`:

```
mia-aux.duckdns.org {
    handle /notificacao* { reverse_proxy 127.0.0.1:8099 }
    handle /saude        { reverse_proxy 127.0.0.1:8099 }
}
```

E `sudo systemctl reload caddy`.

### Tarefas agendadas

```bash
crontab -e
```

```cron
0  20 * * * cd /opt/mia_routines && .venv/bin/python main_triage.py resumo
0  *  * * * cd /opt/mia_routines && .venv/bin/python main_triage.py pulso
30 *  * * * cd /opt/mia_routines && .venv/bin/python main_triage.py limpar
```

O resumo às 20:00, a pulsação e a limpeza de hora a hora.

---

## Os dois comandos para decorar

```bash
systemctl status triagem     # está viva?
sudo systemctl restart triagem   # volta a pôr de pé
```

E, sem estar no VPS, do telemóvel: abre
`https://mia-aux.duckdns.org/saude` no browser. Se responder `{"estado":"vivo"}`,
está de pé.

---

## O teste dos três dias

Configuração: **um** grupo que hoje tenhas silenciado — o da igreja ou o da
família — conforme o resultado do teste de bancada. **Todos os outros ficam
como estão**, para não perderes os avisos de trabalho enquanto a triagem ainda
não está afinada.

**Ao fim de cada dia escreves à mão, no telemóvel, o que te importou nesse
dia** — cinco linhas, sem olhar para o registo.

No fim cruza-se com o que o serviço capturou. A pergunta é **«apanhou o que
importava?»**, não «apanhou tudo?». Cem notificações inúteis não fazem mal
nenhum; uma proposta de trabalho perdida faz. O que faltar, classifica-se
porquê — truncado, agrupado, áudio, imagem, ou conversa em silêncio.

Para ver o que foi capturado:

```bash
sqlite3 /opt/mia_routines/data/triagem.db \
  "SELECT recebido_utc, conversa, categoria, triabilidade, substr(texto,1,60) FROM mensagens ORDER BY id DESC LIMIT 50;"

sqlite3 /opt/mia_routines/data/triagem.db \
  "SELECT triabilidade, COUNT(*) FROM mensagens GROUP BY triabilidade;"
```

A segunda consulta é a que interessa: diz quantas notificações chegaram sem
texto utilizável, e porquê.

---

## O que vai correr mal, e o que acontece em cada caso

| O quê | O que acontece | O que o sistema faz |
|---|---|---|
| Telemóvel sem bateria ou sem rede | Não chega nada | A **pulsação** avisa-te ao fim de 3 horas de silêncio em horário útil |
| Android mata a app | Igual, e em silêncio | A mesma pulsação. É a única defesa, por isso não saltes o passo da bateria |
| Telemóvel reinicia | A app tem de arrancar sozinha | Confirma nas definições dela que arranca no boot; a pulsação apanha se não arrancar |
| VPS reinicia | O serviço volta sozinho | `Restart=always` no systemd |
| Falha de rede a meio | A app tenta outra vez | O NotificationForwarder tem fila e repetição |
| A API do modelo falha | Nada se perde | Tudo cai em «requer resposta» e vais ver no resumo |
| Muitas mensagens num dia | A fatura não dispara | Teto diário de chamadas; passado o teto, só regras |
| Notificação repetida pelo Android | Não te avisa duas vezes | Filtro de duplicados de 90 segundos |
| Atualização do WhatsApp | O «Silencioso» de canal pode-se perder | Vale uma verificação por mês. O «Silencioso» por conversa não sofre disto |

---

## Dados de outras pessoas

As mensagens são de terceiros — família, irmãos da igreja, clientes.

- O texto original é apagado ao fim de **48 horas** (`TRIAGEM_RETER_HORAS`).
  A classificação fica, o texto não.
- A limpeza corre de hora a hora e não depende de ninguém se lembrar.
- Com `TRIAGEM_GUARDAR_TEXTO=0` o texto **nunca** é escrito em disco. Os avisos
  urgentes continuam a funcionar; o resumo nomeia a conversa sem a citar.
- O texto das mensagens que sobrevivem às regras é enviado à Anthropic para
  classificação. Mais ninguém: o telemóvel fala só com o teu VPS, e o VPS só
  com a Anthropic e com o Telegram.
- Os registos do serviço **nunca** incluem o corpo das mensagens.
