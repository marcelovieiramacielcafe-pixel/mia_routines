#!/usr/bin/env bash
# Instala o Agent Reach numa venv isolada e roda a verificação read-only.
# Nada fora de ~/.agent-reach-venv e ~/.config/yt-dlp é alterado sem --system.
# Documentação e notas de segurança: docs/agent-reach.md
set -euo pipefail

VENV="${AGENT_REACH_VENV:-$HOME/.agent-reach-venv}"
REPO="https://github.com/Panniantong/agent-reach"
SRC_DIR="${AGENT_REACH_SRC:-$HOME/.agent-reach-src}"
SYSTEM=0
[[ "${1:-}" == "--system" ]] && SYSTEM=1

echo "== 1/4 Conectividade =="
blocked=0
for host in www.youtube.com www.instagram.com r.jina.ai; do
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "https://$host" 2>/dev/null)" || code=000
    if [[ "$code" == "000" ]]; then
        echo "  [X] $host inalcançável"
        blocked=1
    else
        echo "  [ok] $host ($code)"
    fi
done
if (( blocked )); then
    echo
    echo "  A política de rede deste ambiente bloqueia plataformas que o Agent Reach lê."
    echo "  A instalação segue, mas as leituras vão falhar até a rede ser liberada."
    echo "  Detalhes: docs/agent-reach.md"
    echo
fi

echo "== 2/4 Instalando em $VENV =="
# Clonar em vez de baixar o .zip: o archive do GitHub costuma ser barrado por
# proxies que liberam apenas git, e o clone deixa o código auditável em disco.
if [[ -d "$SRC_DIR/.git" ]]; then
    git -C "$SRC_DIR" fetch --depth 1 origin HEAD
    git -C "$SRC_DIR" reset --hard FETCH_HEAD
else
    rm -rf "$SRC_DIR"
    GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 "$REPO" "$SRC_DIR"
fi
[[ -d "$VENV" ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet "$SRC_DIR"
"$VENV/bin/agent-reach" --version
echo "  fonte: $SRC_DIR @ $(git -C "$SRC_DIR" rev-parse --short HEAD)"

echo "== 3/4 Runtime JS do yt-dlp (canal YouTube) =="
if command -v node >/dev/null 2>&1; then
    mkdir -p "$HOME/.config/yt-dlp"
    if grep -qxF -- '--js-runtimes node' "$HOME/.config/yt-dlp/config" 2>/dev/null; then
        echo "  [ok] já configurado"
    else
        echo '--js-runtimes node' >> "$HOME/.config/yt-dlp/config"
        echo "  [ok] '--js-runtimes node' adicionado"
    fi
else
    echo "  [X] Node.js ausente — legendas do YouTube podem falhar"
fi

# O doctor procura yt-dlp e afins no PATH; sem isto ele reporta o que a venv já tem.
export PATH="$VENV/bin:$PATH"

echo "== 4/4 Diagnóstico =="
if (( SYSTEM )); then
    echo "  --system: instalando dependências externas (npm global, etc.)"
    "$VENV/bin/agent-reach" install --env=auto --system
else
    "$VENV/bin/agent-reach" install --env=auto
    echo
    echo "  Modo read-only. Para aplicar as instalações de sistema:"
    echo "    $0 --system"
fi

echo
echo "Pronto. Adicione ao PATH:  export PATH=\"$VENV/bin:\$PATH\""
echo "Instagram exige sessão de Chrome em desktop — leia docs/agent-reach.md antes."
