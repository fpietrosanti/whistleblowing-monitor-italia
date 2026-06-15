#!/usr/bin/env bash
# Provision wbmonitor's Claude Code auth by reusing gl-auditor's OAuth token.
# Run as root (via sudo). Never prints the token.
set -euo pipefail

SRC=/home/gl-auditor/.gl-audit-env
DST=/home/wbmonitor/.wb-discovery-env
CFG=/home/wbmonitor/.config/claude-wb

if [ ! -f "$SRC" ]; then
    echo "ERROR: $SRC not found" >&2
    exit 1
fi

TOK=$(grep -m1 CLAUDE_CODE_OAUTH_TOKEN "$SRC" \
    | sed -E 's/^(export[[:space:]]+)?CLAUDE_CODE_OAUTH_TOKEN=//' \
    | tr -d '"')

if [ -z "$TOK" ]; then
    echo "ERROR: no token value extracted" >&2
    exit 1
fi

mkdir -p "$CFG"
printf 'export CLAUDE_CODE_OAUTH_TOKEN=%s\nexport CLAUDE_CONFIG_DIR=%s\n' "$TOK" "$CFG" > "$DST"
chown -R wbmonitor:wbmonitor "$DST" "$CFG"
chmod 700 "$CFG"
chmod 600 "$DST"

# Report only the length, never the token itself.
echo "OK token_len=${#TOK} dst=$DST cfg=$CFG"
