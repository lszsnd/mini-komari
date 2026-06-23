#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="mini-komari"
INSTALL_DIR="/opt/$APP_NAME"
SERVICE_FILE="/etc/systemd/system/$APP_NAME.service"
REF="${MINI_KOMARI_REF:-main}"
REPO="${MINI_KOMARI_REPO:-}"
RAW_BASE="${MINI_KOMARI_RAW_BASE:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo /tmp)"

MODE="${1:-master}"

info() { echo "✓ $*"; }
warn() { echo "! $*"; }
die() { echo "× $*" >&2; exit 1; }
random_password() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -base64 18 | tr -d '=+/ ' | cut -c1-16
    else
        date +%s%N | sha256sum | cut -c1-16
    fi
}
usage() {
    cat <<'EOF'
Mini Komari installer

用法：
  bash install.sh master [端口] [TOKEN] [主控公网URL]
  bash install.sh agent  <主控URL> [TOKEN] [节点名] [分组]
  bash install.sh standalone [端口]
  bash install.sh update

示例：
  bash install.sh master 6060 mytoken
  bash install.sh agent http://1.2.3.4:6060 mytoken hk-node-1 香港

环境变量：
  MINI_KOMARI_REPO=用户名/仓库
  MINI_KOMARI_REF=main
  MINI_KOMARI_RAW_BASE=https://raw.githubusercontent.com/用户名/仓库/main
EOF
}

need_root() { [ "$(id -u)" -eq 0 ] || die "请用 root 运行"; }

ensure_deps() {
    if command -v python3 >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
        return 0
    fi
    warn "缺少 python3/curl，尝试自动安装..."
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y python3 curl ca-certificates
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y python3 curl ca-certificates
    elif command -v yum >/dev/null 2>&1; then
        yum install -y python3 curl ca-certificates
    elif command -v apk >/dev/null 2>&1; then
        apk add --no-cache python3 curl ca-certificates
    else
        die "无法识别包管理器，请手动安装 python3 curl ca-certificates"
    fi
}

resolve_raw_base() {
    [ -n "$RAW_BASE" ] && return 0
    if [ -n "$REPO" ]; then
        RAW_BASE="https://raw.githubusercontent.com/$REPO/$REF"
    else
        RAW_BASE="https://raw.githubusercontent.com/lszsnd/mini-komari/$REF"
    fi
}

detect_public_ip() {
    local ip=""
    for url in \
        https://api.ipify.org \
        https://ifconfig.me/ip \
        https://icanhazip.com; do
        ip="$(curl -fsS --max-time 4 "$url" 2>/dev/null | tr -d '[:space:]' || true)"
        if printf '%s' "$ip" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; then
            printf '%s\n' "$ip"
            return 0
        fi
    done
    hostname -I 2>/dev/null | awk '{print $1}'
}

fetch_app() {
    mkdir -p "$INSTALL_DIR"
    if [ -f "$SCRIPT_DIR/mini_komari.py" ]; then
        info "使用本地 mini_komari.py"
        install -m 0755 "$SCRIPT_DIR/mini_komari.py" "$INSTALL_DIR/mini_komari.py"
    else
        resolve_raw_base
        info "从 $RAW_BASE/mini_komari.py 下载探针"
        curl -fsSL "$RAW_BASE/mini_komari.py" -o "$INSTALL_DIR/mini_komari.py"
        chmod 0755 "$INSTALL_DIR/mini_komari.py"
    fi
    python3 -m py_compile "$INSTALL_DIR/mini_komari.py"
}

write_master_service() {
    local port="${2:-${MINI_KOMARI_PORT:-6060}}"
    local token="${3:-${MINI_KOMARI_TOKEN:-}}"
    local public_url="${4:-${MINI_KOMARI_PUBLIC_URL:-}}"
    resolve_raw_base
    if [ -z "$public_url" ]; then
        local detected_ip
        detected_ip="$(detect_public_ip)"
        [ -n "$detected_ip" ] || detected_ip="你的主控IP"
        public_url="http://$detected_ip:$port"
    fi
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Mini Komari Master
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=MINI_KOMARI_TOKEN=$token
Environment=MINI_KOMARI_PUBLIC_URL=$public_url
Environment=MINI_KOMARI_RAW_BASE=$RAW_BASE
Environment=MINI_KOMARI_DATA_FILE=$INSTALL_DIR/nodes.json
Environment=MINI_KOMARI_USER_FILE=$INSTALL_DIR/user.json
ExecStart=/usr/bin/env python3 $INSTALL_DIR/mini_komari.py master --host 0.0.0.0 --port $port --token $token --public-url $public_url --raw-base $RAW_BASE --data-file $INSTALL_DIR/nodes.json --user-file $INSTALL_DIR/user.json --quiet
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only

[Install]
WantedBy=multi-user.target
EOF
    echo "$port" > "$INSTALL_DIR/PORT"
    echo "master" > "$INSTALL_DIR/MODE"
    echo "$public_url" > "$INSTALL_DIR/PUBLIC_URL"
}

write_agent_service() {
    local master_url="${2:-}"
    local token="${3:-${MINI_KOMARI_TOKEN:-}}"
    local node_name="${4:-${MINI_KOMARI_NODE_NAME:-$(hostname)}}"
    local node_group="${5:-${MINI_KOMARI_NODE_GROUP:-默认}}"
    [ -n "$master_url" ] || die "agent 模式需要主控 URL，例如：http://1.2.3.4:6060"
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Mini Komari Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment="MINI_KOMARI_TOKEN=$token"
Environment="MINI_KOMARI_NODE_NAME=$node_name"
Environment="MINI_KOMARI_NODE_GROUP=$node_group"
ExecStart=/usr/bin/env python3 $INSTALL_DIR/mini_komari.py agent --master $master_url --interval ${MINI_KOMARI_INTERVAL:-5} --quiet
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only

[Install]
WantedBy=multi-user.target
EOF
    echo "agent" > "$INSTALL_DIR/MODE"
    echo "$master_url" > "$INSTALL_DIR/MASTER_URL"
    echo "$token" > "$INSTALL_DIR/TOKEN"
    echo "$node_name" > "$INSTALL_DIR/NODE_NAME"
    echo "$node_group" > "$INSTALL_DIR/NODE_GROUP"
}

write_standalone_service() {
    local port="${2:-${MINI_KOMARI_PORT:-6060}}"
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Mini Komari Standalone
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/env python3 $INSTALL_DIR/mini_komari.py standalone --host 0.0.0.0 --port $port --data-file $INSTALL_DIR/nodes.json --quiet
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only

[Install]
WantedBy=multi-user.target
EOF
    echo "$port" > "$INSTALL_DIR/PORT"
    echo "standalone" > "$INSTALL_DIR/MODE"
}

update_app() {
    [ -f "$SERVICE_FILE" ] || die "未发现 $SERVICE_FILE，请先安装 mini-komari"
    fetch_app
    systemctl daemon-reload
    systemctl restart "$APP_NAME"
    info "更新完成，已重启 $APP_NAME"
}

agent_once_check() {
    local master_url="$(cat "$INSTALL_DIR/MASTER_URL" 2>/dev/null || true)"
    local token="$(cat "$INSTALL_DIR/TOKEN" 2>/dev/null || true)"
    local node_name="$(cat "$INSTALL_DIR/NODE_NAME" 2>/dev/null || hostname)"
    local node_group="$(cat "$INSTALL_DIR/NODE_GROUP" 2>/dev/null || printf '默认')"
    [ -n "$master_url" ] || return 1
    MINI_KOMARI_TOKEN="$token" MINI_KOMARI_NODE_NAME="$node_name" MINI_KOMARI_NODE_GROUP="$node_group" \
        /usr/bin/env python3 "$INSTALL_DIR/mini_komari.py" agent --master "$master_url" --once --interval 1
}

post_install_check() {
    local mode="$1"
    local port="${2:-${MINI_KOMARI_PORT:-6060}}"
    local health_url="http://127.0.0.1:$port/health"
    case "$mode" in
        master|standalone)
            for _ in $(seq 1 20); do
                if curl -fsS --max-time 2 "$health_url" >/dev/null 2>&1; then
                    info "健康检查通过：$health_url"
                    return 0
                fi
                sleep 0.5
            done
            warn "健康检查失败：$health_url"
            warn "请查看日志：journalctl -u $APP_NAME -f"
            return 1
            ;;
        agent)
            if ! systemctl is-active --quiet "$APP_NAME"; then
                warn "Agent 服务未处于 active 状态"
                warn "请查看日志：journalctl -u $APP_NAME -f"
                return 1
            fi
            info "Agent 服务已启动"
            if agent_once_check; then
                info "Agent 上报验证通过"
                return 0
            fi
            warn "Agent 上报验证失败：主控地址、TOKEN 或网络可能有问题"
            warn "请查看日志：journalctl -u $APP_NAME -f"
            return 1
            ;;
    esac
}

main() {
    case "$MODE" in -h|--help|help) usage; exit 0 ;; esac
    need_root
    ensure_deps
    if [ "$MODE" = "update" ]; then
        update_app
        echo "  状态: systemctl status $APP_NAME"
        echo "  日志: journalctl -u $APP_NAME -f"
        exit 0
    fi
    fetch_app

    case "$MODE" in
        master) write_master_service "$@" ;;
        agent) write_agent_service "$@" ;;
        standalone) write_standalone_service "$@" ;;
        *) usage; die "未知模式：$MODE" ;;
    esac

    systemctl daemon-reload
    systemctl enable --now "$APP_NAME"
    sleep 1
    post_install_check "$MODE" "${2:-${MINI_KOMARI_PORT:-6060}}" || true

    echo
    info "安装完成：$MODE"
    case "$MODE" in
        master)
            echo "  面板: $(cat "$INSTALL_DIR/PUBLIC_URL" 2>/dev/null || printf '%s' "${4:-${MINI_KOMARI_PUBLIC_URL:-http://你的主控IP:${2:-${MINI_KOMARI_PORT:-6060}}}}")/"
            echo "  首次打开网页后，请注册管理员账号并登录"
            echo "  打开主控网页后，可直接在页面里生成 Agent 安装命令"
            ;;
        agent)
            echo "  正在上报到: ${2:-}"
            ;;
        standalone)
            echo "  面板: http://你的服务器IP:${2:-${MINI_KOMARI_PORT:-6060}}/"
            ;;
    esac
    echo "  状态: systemctl status $APP_NAME"
    echo "  日志: journalctl -u $APP_NAME -f"
}

main "$@"
