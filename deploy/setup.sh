#!/usr/bin/env bash
#
# Sets up the bot on a fresh Ubuntu/Debian VPS, reachable over HTTPS.
#
#   curl -fsSL <raw-url>/deploy/setup.sh | sudo bash
#   # or, from a clone:
#   sudo bash deploy/setup.sh
#
# Afterwards the dashboard answers at https://<your-ip>.sslip.io and the
# bot keeps running across reboots.

set -euo pipefail

REPO="${REPO:-https://github.com/gualax/boticloude}"
BRANCH="${BRANCH:-claude/polymarket-trading-bot-E5ca7}"
APP_DIR="/opt/boticloude"
APP_USER="boticloude"

die() { echo "ERROR: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

[[ $EUID -eq 0 ]] || die "Ejecuta con sudo."

# ── Where will this be reachable? ────────────────────────────────────────
PUBLIC_IP="$(curl -fsS --max-time 10 https://api.ipify.org || true)"
[[ -n "$PUBLIC_IP" ]] || die "No pude averiguar la IP publica de esta maquina."

# sslip.io resolves an IP embedded in the hostname back to that IP, which
# is enough for Let's Encrypt to validate — real HTTPS without a domain.
DOMAIN="${DOMAIN:-${PUBLIC_IP}.sslip.io}"

echo "IP publica : $PUBLIC_IP"
echo "Dominio    : $DOMAIN"

# ── Credentials ──────────────────────────────────────────────────────────
if [[ -z "${DASHBOARD_PASSWORD:-}" ]]; then
    read -rsp "Contrasena para el panel: " DASHBOARD_PASSWORD; echo
fi
[[ -n "$DASHBOARD_PASSWORD" ]] || die "La contrasena no puede estar vacia."
[[ ${#DASHBOARD_PASSWORD} -ge 8 ]] || die "Usa al menos 8 caracteres."

if [[ -z "${GEMINI_API_KEY:-}" ]]; then
    read -rsp "GEMINI_API_KEY (Enter para omitir): " GEMINI_API_KEY; echo
fi

# ── System packages ──────────────────────────────────────────────────────
step "Instalando paquetes"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl \
    debian-keyring debian-archive-keyring apt-transport-https ufw

if ! command -v caddy >/dev/null; then
    step "Instalando Caddy (HTTPS automatico)"
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
        > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
    apt-get install -y -qq caddy
fi

# ── Application user and code ────────────────────────────────────────────
step "Preparando la aplicacion"
id -u "$APP_USER" &>/dev/null || useradd --system --home "$APP_DIR" \
    --shell /usr/sbin/nologin "$APP_USER"

if [[ -d "$APP_DIR/.git" ]]; then
    git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
    git -C "$APP_DIR" checkout --quiet -B "$BRANCH" "origin/$BRANCH"
else
    rm -rf "$APP_DIR"
    git clone --quiet --branch "$BRANCH" "$REPO" "$APP_DIR"
fi

python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# ── Configuration ────────────────────────────────────────────────────────
step "Escribiendo configuracion"
SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"

# Written by heredoc with the password quoted, so spaces and symbols survive
cat > "$APP_DIR/.env" <<EOF
PAPER_TRADING=true
INITIAL_BALANCE=1000
CRYPTO_ENABLED=true

GEMINI_API_KEY=${GEMINI_API_KEY}
ANALYSIS_HOUR=23

HERMES_ENABLED=true
HERMES_MODEL=gemini-3.1-pro-preview
HERMES_REFLECT_EVERY_N_TRADES=10

DASHBOARD_USER=admin
DASHBOARD_PASSWORD='${DASHBOARD_PASSWORD}'
SECRET_KEY=${SECRET_KEY}
EOF

mkdir -p "$APP_DIR/data"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"

# ── Services ─────────────────────────────────────────────────────────────
step "Instalando servicios"
install -m 644 "$APP_DIR/deploy/boticloude.service" \
    /etc/systemd/system/boticloude.service

mkdir -p /var/log/caddy && chown caddy:caddy /var/log/caddy
DOMAIN="$DOMAIN" envsubst < "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile \
    2>/dev/null || sed "s|{\$DOMAIN}|$DOMAIN|" "$APP_DIR/deploy/Caddyfile" \
    > /etc/caddy/Caddyfile

systemctl daemon-reload
systemctl enable --now boticloude
systemctl restart caddy

# ── Firewall ─────────────────────────────────────────────────────────────
step "Configurando cortafuegos"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null    # Let's Encrypt validation
ufw allow 443/tcp >/dev/null   # the dashboard
# Port 8080 stays closed: gunicorn listens on loopback only, behind Caddy.
ufw --force enable >/dev/null

# ── Done ─────────────────────────────────────────────────────────────────
sleep 3
step "Listo"
echo
echo "  Panel    : https://${DOMAIN}"
echo "  Usuario  : admin"
echo "  Password : la que acabas de escribir"
echo
echo "  Estado   : systemctl status boticloude"
echo "  Logs     : journalctl -u boticloude -f"
echo "  Reiniciar: systemctl restart boticloude"
echo
if ! systemctl is-active --quiet boticloude; then
    echo "  AVISO: el servicio no arranco. Mira:"
    echo "         journalctl -u boticloude -n 50 --no-pager"
fi
