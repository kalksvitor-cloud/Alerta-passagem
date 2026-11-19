#!/usr/bin/env bash
# Carrega as chaves do arquivo .env e roda o comando. Ex.: ./iniciar.sh bot
cd "$(dirname "$0")"
set -a; [ -f .env ] && . ./.env; set +a
exec python3 passagens.py "$@"
