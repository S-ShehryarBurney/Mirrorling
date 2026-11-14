#!/usr/bin/env bash

set -e

PROJECT_DIR="$HOME/Desktop/Data/The_Core/Mirrorling"

if ! command -v gnome-terminal >/dev/null 2>&1; then
    echo "GNOME Terminal was not found. Tell me which terminal Zorin is using."
    exit 1
fi

# Backend window
gnome-terminal --title="Mirrorling - Backend" -- bash -lc '
cd "$HOME/Desktop/Data/The_Core/Mirrorling/backend" || exit 1
source .venv/bin/activate || { echo "Backend environment failed to activate."; exec bash; }

echo "Starting Mirrorling Backend..."
uvicorn main:app --reload

echo
echo "Backend stopped. This window will remain open."
exec bash
'

# Frontend window
gnome-terminal --title="Mirrorling - Frontend" -- bash -lc '
export NVM_DIR="$HOME/.var/app/com.visualstudio.code/config/nvm"

if [ ! -s "$NVM_DIR/nvm.sh" ]; then
    echo "NVM was not found at: $NVM_DIR"
    exec bash
fi

. "$NVM_DIR/nvm.sh"
nvm use --lts || { echo "Could not select Node.js LTS."; exec bash; }

cd "$HOME/Desktop/Data/The_Core/Mirrorling/frontend" || exit 1

echo "Starting Mirrorling Frontend..."
npm run dev

echo
echo "Frontend stopped. This window will remain open."
exec bash
'
