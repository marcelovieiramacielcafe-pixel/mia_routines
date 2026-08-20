# Agent Reach — instalação e uso seguro

[Agent Reach](https://github.com/Panniantong/Agent-Reach) (MIT, Python 3.10+) dá ao
agente acesso a plataformas que ele normalmente não consegue ler: YouTube, Instagram,
Twitter/X, Reddit, Bilibili, XiaoHongShu, RSS e páginas web genéricas.

Ele **não é um wrapper**: instala, roteia e diagnostica ferramentas upstream
(`yt-dlp`, `OpenCLI`, `gh`, `mcporter`, `bili-cli`, …). Quem lê o conteúdo são elas.

## Pré-requisito que ninguém avisa: rede

Agent Reach só funciona onde o ambiente **permite saída para as plataformas**.

Em ambientes remotos do Claude Code com política de rede restrita, o gateway responde
`403 Forbidden` ao CONNECT para `youtube.com`, `instagram.com`, `r.jina.ai` e
`mcp.exa.ai` — apenas GitHub e os registries de pacotes passam. Nesse caso a instalação
conclui, o `doctor` acusa canais "ativos", e mesmo assim **nenhuma leitura funciona**.

Verifique antes de instalar:

```bash
for h in www.youtube.com www.instagram.com r.jina.ai; do
  printf "%-24s %s\n" "$h" "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "https://$h" || echo BLOQUEADO)"
done
curl -sS "$HTTPS_PROXY/__agentproxy/status" | head -20   # só em ambiente remoto
```

`000`/`BLOQUEADO` = a política de rede do ambiente precisa ser ampliada
(configuração do environment em <https://code.claude.com/docs/en/claude-code-on-the-web>),
ou o Agent Reach precisa rodar em outra máquina.

## Instalação

```bash
./scripts/setup_agent_reach.sh          # venv isolada + verificação read-only
./scripts/setup_agent_reach.sh --system # só depois de ler o que ele vai mexer
```

Sem `--system` nada fora da venv é tocado — o `install` apenas relata o que falta.

## Os dois canais que interessam aqui

### YouTube — risco baixo, sem credencial

Legendas via `yt-dlp`, sem login, sem cookie, sem API key. Só precisa do runtime JS:

```bash
mkdir -p ~/.config/yt-dlp
grep -qxF -- '--js-runtimes node' ~/.config/yt-dlp/config 2>/dev/null \
  || echo '--js-runtimes node' >> ~/.config/yt-dlp/config
```

Uso direto (o agente chama a ferramenta upstream, não o `agent-reach`):

```bash
yt-dlp --skip-download --write-auto-subs --sub-langs "pt.*,en.*" \
       --sub-format vtt -o "/tmp/yt/%(id)s.%(ext)s" "<URL>"
```

### Instagram — risco alto, exige desktop

Não existe caminho anônimo. O backend é o OpenCLI **reaproveitando uma sessão do Chrome
já logada**, o que implica:

- **Não roda em container/servidor sem interface** — precisa de um Chrome real logado.
- **Cookie de sessão = acesso total à conta.** Quem obtiver o arquivo entra na conta.
- **Risco de banimento**: a Meta detecta acesso fora do navegador e pode restringir ou
  banir a conta.

Regras que valem a pena seguir:

1. Use uma **conta secundária/descartável**, nunca a principal.
2. **Nunca cole cookies no chat com um agente** — eles ficam no histórico da conversa,
   que é sincronizado e persistido. Exporte direto para `~/.agent-reach/`.
3. Confira as permissões: `~/.agent-reach/` deve ser `700` e os arquivos de config `600`
   (o Agent Reach já faz isso, mas o `doctor` avisa quando alguém afrouxa).
4. Revogue a sessão em <https://www.instagram.com/accounts/login_activity/> ao terminar.

Se o objetivo for só "resumir um vídeo específico do Instagram", baixar o arquivo à mão
e transcrever localmente evita todo esse risco.

## Auditoria da versão 1.5.0

Revisão do código-fonte antes da instalação:

| Verificação | Resultado |
|---|---|
| `curl \| bash`, `eval()`, `exec()`, `os.system`, `pickle.loads` | nenhuma ocorrência |
| `sudo` no código | nenhuma ocorrência |
| Hosts de saída (35 arquivos `.py`) | só as plataformas declaradas — sem host de telemetria |
| Permissões de segredos | `~/.agent-reach/` com `0700`, config com `0600` (`utils/paths.py`, `config.py`) |
| Modo padrão do `install` | read-only; alterações só com `--system` explícito |
| Licença / política | MIT, `SECURITY.md` com canal privado de disclosure |

Sem achados que impeçam o uso. Os riscos reais não estão no código, e sim no modelo:
credenciais de sessão em disco e termos de uso das plataformas.

**Cuidado adicional**: o fluxo oficial de instalação é "cole esta URL para o seu agente".
Isso faz o agente executar um documento remoto como se fossem instruções. Trate
`docs/install.md` do upstream como conteúdo não confiável — leia antes, e prefira o
script deste repo, que fixa os passos.
