#!/usr/bin/env fish
set ROOT_DIR (cd (dirname (status --current-filename)) && pwd)

bash -lc "export NVM_DIR=\"$HOME/.nvm\"; . \"\$NVM_DIR/nvm.sh\"; nvm install 20 >/dev/null; nvm use 20 >/dev/null; cd \"$ROOT_DIR/src/frontend\"; npm install; exec npm run dev -- --host 0.0.0.0"
