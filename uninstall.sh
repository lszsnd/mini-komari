#!/usr/bin/env bash
set -Eeuo pipefail
APP_NAME="mini-komari"
[ "$(id -u)" -eq 0 ] || { echo "请用 root 运行" >&2; exit 1; }
systemctl disable --now "$APP_NAME" 2>/dev/null || true
rm -f "/etc/systemd/system/$APP_NAME.service"
systemctl daemon-reload 2>/dev/null || true
rm -rf "/opt/$APP_NAME"
echo "已卸载 $APP_NAME"
