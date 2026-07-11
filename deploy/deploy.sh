#!/bin/bash
# English Coach Deployment Script
# Usage: ./deploy.sh [install|update|start|stop|status]

set -e

INSTALL_DIR="/opt/english-coach"
SERVICE_NAME="english-coach"
USER_NAME="english-coach"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root"
        exit 1
    fi
}

install_dependencies() {
    log_info "Installing system dependencies..."
    apt-get update
    apt-get install -y python3.12 python3.12-venv nginx

    # Install uv
    if ! command -v uv &> /dev/null; then
        log_info "Installing uv..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
    fi
}

create_user() {
    if ! id "$USER_NAME" &>/dev/null; then
        log_info "Creating service user..."
        useradd --system --shell /bin/false --home-dir "$INSTALL_DIR" "$USER_NAME"
    fi
}

setup_directories() {
    log_info "Setting up directories..."
    mkdir -p "$INSTALL_DIR"
    mkdir -p "$INSTALL_DIR/data/audio"
    mkdir -p "$INSTALL_DIR/data/models"
    mkdir -p "$INSTALL_DIR/logs"
}

install_backend() {
    log_info "Installing backend..."
    cd "$INSTALL_DIR"

    # Clone or update repository
    if [[ -d ".git" ]]; then
        git pull
    else
        git clone https://github.com/mohamedalifaragitiai/AI-English-Coach.git .
    fi

    # Create virtual environment and install dependencies
    uv venv
    uv sync

    # Copy environment file
    if [[ ! -f ".env" ]]; then
        cp .env.example .env
        log_warn "Edit $INSTALL_DIR/.env with your configuration"
    fi
}

install_frontend() {
    log_info "Installing frontend..."
    cd "$INSTALL_DIR/frontend"

    npm ci --production
    npm run build
}

setup_models() {
    log_info "Downloading AI models..."
    cd "$INSTALL_DIR"
    .venv/bin/python scripts/setup_models.py
}

setup_systemd() {
    log_info "Setting up systemd service..."
    cp deploy/english-coach.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
}

setup_nginx() {
    log_info "Setting up nginx..."
    cp deploy/nginx.conf /etc/nginx/sites-available/english-coach
    ln -sf /etc/nginx/sites-available/english-coach /etc/nginx/sites-enabled/
    nginx -t
    systemctl reload nginx
}

set_permissions() {
    log_info "Setting permissions..."
    chown -R "$USER_NAME:$USER_NAME" "$INSTALL_DIR"
    chmod -R 750 "$INSTALL_DIR"
}

install() {
    check_root
    log_info "Starting installation..."

    install_dependencies
    create_user
    setup_directories
    install_backend
    install_frontend
    setup_models
    setup_systemd
    setup_nginx
    set_permissions

    log_info "Installation complete!"
    log_info "Start the service with: systemctl start $SERVICE_NAME"
}

update() {
    check_root
    log_info "Updating English Coach..."

    systemctl stop "$SERVICE_NAME" || true

    cd "$INSTALL_DIR"
    git pull
    uv sync

    cd frontend
    npm ci --production
    npm run build

    set_permissions
    systemctl start "$SERVICE_NAME"

    log_info "Update complete!"
}

start() {
    check_root
    log_info "Starting English Coach..."
    systemctl start "$SERVICE_NAME"
    log_info "Service started"
}

stop() {
    check_root
    log_info "Stopping English Coach..."
    systemctl stop "$SERVICE_NAME"
    log_info "Service stopped"
}

status() {
    systemctl status "$SERVICE_NAME" --no-pager
    echo ""
    log_info "Checking health endpoint..."
    curl -s http://localhost:8000/health | python3 -m json.tool || log_warn "Backend not responding"
}

case "$1" in
    install)
        install
        ;;
    update)
        update
        ;;
    start)
        start
        ;;
    stop)
        stop
        ;;
    status)
        status
        ;;
    *)
        echo "Usage: $0 {install|update|start|stop|status}"
        exit 1
        ;;
esac
