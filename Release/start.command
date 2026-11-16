#!/bin/sh
# Starts the bridge: double-click in Finder, or ./start.command [--sim | --dump 3 | --install-plugin | ...]
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || ./setup.sh || exit 1
exec .venv/bin/python push_resolume_bridge.py "$@"
