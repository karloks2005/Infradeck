#!/usr/bin/env bash
set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

export NVM_DIR="$HOME/.nvm"
if [ -s "$NVM_DIR/nvm.sh" ]; then
  . "$NVM_DIR/nvm.sh"
fi

nvm install 20 >/dev/null
nvm use 20 >/dev/null

cd "$ROOT_DIR/src/frontend"
npm install
npm run dev -- --host 0.0.0.0
