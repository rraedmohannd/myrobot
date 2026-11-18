#!/usr/bin/env bash
# Installs the Smart Campus Robot backend on a fresh server (Ubuntu 22.04/24.04, Debian 12/13):
#
#     sudo git clone https://github.com/rraedmohannd/myrobot.git /opt/smart-campus-robot
#     sudo bash /opt/smart-campus-robot/deploy/server/install.sh [--domain robot.example.org]
#
# Steps (each one is safe to run again):
#   1. packages: ca-certificates curl git openssl jq ufw unattended-upgrades
#   2. Docker Engine + compose plugin from Docker's official apt repository
#   3. firewall (ufw): SSH, 80/tcp, 443/tcp, 443/udp; everything else incoming is denied
#   4. automatic security updates (unattended-upgrades)
#   5. the code in /opt/smart-campus-robot (kept if it is already a git checkout: use update.sh)
#   6. deploy/server/.env from .env.production.example with GENERATED secrets; an existing .env is
#      kept, only missing keys are added (secrets are never regenerated). The domain is asked (empty
#      = <public-ip>.sslip.io), the OpenAI key is asked with hidden input and stored only in .env
#   7. build and start the stack, wait for the health check, print the URLs and the next steps
#
# Options:
#   --domain NAME          the public name (DNS A record to this server); default: ask, or sslip.io
#   --no-caddy             behind an existing web server (nginx): no Caddy, the backend listens on
#                          BACKEND_BIND (127.0.0.1:8000); see docs/DEPLOY.md "Server mode"
#   --repo URL             git URL to clone (default https://github.com/rraedmohannd/myrobot.git);
#                          git@github.com:... works with --deploy-key
#   --deploy-key FILE      SSH private key of a read-only GitHub deploy key (for git@ URLs)
#   --ref REF              branch or tag to clone (default main)
#   --ssh-port N           the SSH port to keep open in the firewall (default 22)
#   --ask-openai-key       ask for the OpenAI key again even if .env already has one
#   --skip-docker-install  Docker is already installed
#   --skip-firewall        do not touch ufw
#   --no-start             prepare everything but do not build or start the stack
#   --yes                  no questions: domain from --domain or sslip.io, OpenAI key from the file
#                          named by OPENAI_API_KEY_FILE (or left empty)
#   --force                run on an untested operating system
# Log (no secrets in it): /var/log/smart-campus-install.log

set -euo pipefail
LIB="$(dirname "${BASH_SOURCE[0]}")/lib.sh"
[[ -f "$LIB" ]] || { echo "!! $LIB not found: run install.sh from a checkout of the repository" >&2; exit 1; }
# shellcheck source=deploy/server/lib.sh
source "$LIB"

TARGET=/opt/smart-campus-robot
REPO_URL=https://github.com/rraedmohannd/myrobot.git
REF=main
DOMAIN_ARG=""
NO_CADDY=0
DEPLOY_KEY=""
SSH_PORT=22
ASK_KEY=0
SKIP_DOCKER=0
SKIP_FW=0
NO_START=0
YES=0
FORCE=0
LOG=/var/log/smart-campus-install.log

need_value() { [[ $1 -ge 2 && -n "$2" ]] || die "$3 needs a value"; }
while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain) need_value $# "${2:-}" "$1"; DOMAIN_ARG="$2"; shift 2 ;;
        --no-caddy) NO_CADDY=1; shift ;;
        --repo) need_value $# "${2:-}" "$1"; REPO_URL="$2"; shift 2 ;;
        --deploy-key) need_value $# "${2:-}" "$1"; DEPLOY_KEY="$2"; shift 2 ;;
        --ref) need_value $# "${2:-}" "$1"; REF="$2"; shift 2 ;;
        --ssh-port) need_value $# "${2:-}" "$1"; SSH_PORT="$2"; shift 2 ;;
        --ask-openai-key) ASK_KEY=1; shift ;;
        --skip-docker-install) SKIP_DOCKER=1; shift ;;
        --skip-firewall) SKIP_FW=1; shift ;;
        --no-start) NO_START=1; shift ;;
        --yes) YES=1; shift ;;
        --force) FORCE=1; shift ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done
[[ "$SSH_PORT" =~ ^[0-9]{1,5}$ ]] || die "--ssh-port: a number"
DOMAIN_RE='^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$'
if [[ -n "$DOMAIN_ARG" ]]; then
    DOMAIN_ARG="$(printf '%s' "$DOMAIN_ARG" | tr '[:upper:]' '[:lower:]')"
    [[ "$DOMAIN_ARG" =~ $DOMAIN_RE ]] || die "--domain: not a host name: $DOMAIN_ARG"
fi
[[ -z "$DEPLOY_KEY" || -f "$DEPLOY_KEY" ]] || die "--deploy-key: file not found: $DEPLOY_KEY"
require_root

# Everything printed goes to the terminal and to the log. No secret is ever printed: the OpenAI
# key is read with hidden input, generated secrets are written straight into .env, and the access
# code is shown on the terminal device only (not in the log).
touch "$LOG"
chmod 600 "$LOG"
exec > >(tee -a "$LOG") 2>&1
echo "---- install.sh $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"

# ---------------------------------------------------------------- 0. operating system
# shellcheck source=/dev/null
OS_ID="$(. /etc/os-release && echo "${ID:-}")"
# shellcheck source=/dev/null
OS_VER="$(. /etc/os-release && echo "${VERSION_ID:-}")"
# shellcheck source=/dev/null
OS_CODENAME="$(. /etc/os-release && echo "${VERSION_CODENAME:-}")"
case "$OS_ID:$OS_VER" in
    ubuntu:22.04 | ubuntu:24.04 | debian:12 | debian:13) note "system: $OS_ID $OS_VER ($OS_CODENAME)" ;;
    *)
        [[ $FORCE -eq 1 ]] || die "untested system $OS_ID $OS_VER (Ubuntu 22.04/24.04 or Debian 12/13): --force to try anyway"
        warn "untested system $OS_ID $OS_VER: continuing because of --force"
        ;;
esac
export DEBIAN_FRONTEND=noninteractive
HAVE_SYSTEMD=0
if [[ -d /run/systemd/system ]]; then HAVE_SYSTEMD=1; fi

# ---------------------------------------------------------------- 1. packages
info "1/7 packages"
apt-get update -q
apt-get install -y -q --no-install-recommends ca-certificates curl git openssl jq ufw unattended-upgrades

# ---------------------------------------------------------------- 2. Docker
info "2/7 Docker Engine + compose plugin (Docker's apt repository)"
if [[ $SKIP_DOCKER -eq 1 ]]; then
    note "skipped (--skip-docker-install)"
else
    for p in docker.io docker-compose docker-compose-v2 podman-docker containerd runc; do
        if dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed'; then
            die "the distribution package $p is installed and conflicts with Docker's packages: remove it (apt-get remove $p) or use --skip-docker-install"
        fi
    done
    case "$OS_ID" in
        ubuntu | debian) DOCKER_DIST="$OS_ID" ;;
        *) DOCKER_DIST=ubuntu; warn "assuming Docker's ubuntu repository for $OS_ID" ;;
    esac
    install -m 0755 -d /etc/apt/keyrings
    if [[ ! -s /etc/apt/keyrings/docker.asc ]]; then
        curl -fsSL "https://download.docker.com/linux/$DOCKER_DIST/gpg" -o /etc/apt/keyrings/docker.asc
        chmod a+r /etc/apt/keyrings/docker.asc
    fi
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$DOCKER_DIST $OS_CODENAME stable" \
        >/etc/apt/sources.list.d/docker.list
    apt-get update -q
    apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    if [[ $HAVE_SYSTEMD -eq 1 ]]; then
        systemctl enable --now docker
    else
        warn "no systemd here (a container?): start dockerd yourself"
    fi
fi
docker --version || warn "docker not found"
docker compose version || warn "docker compose plugin not found"

# ---------------------------------------------------------------- 3. firewall
info "3/7 firewall (ufw)"
if [[ $SKIP_FW -eq 1 ]]; then
    note "skipped (--skip-firewall)"
else
    # Refuse to lock ourselves out: sshd must listen on the port we keep open.
    if command -v sshd >/dev/null; then
        ports="$(sshd -T 2>/dev/null | awk '$1 == "port" {print $2}' | tr '\n' ' ')"
        if [[ -n "$ports" && " $ports" != *" $SSH_PORT "* ]]; then
            die "sshd listens on port(s) ${ports% }, not $SSH_PORT: run again with --ssh-port N (or --skip-firewall)"
        fi
    fi
    note "keeping SSH open on port $SSH_PORT (use --ssh-port if your SSH server uses another port)"
    ufw allow "$SSH_PORT/tcp" comment 'SSH'
    ufw allow 80/tcp comment 'HTTP (certificates, redirect)'
    ufw allow 443/tcp comment 'HTTPS'
    ufw allow 443/udp comment 'HTTP/3'
    ufw default deny incoming
    ufw default allow outgoing
    ufw --force enable
    ufw status verbose | sed 's/^/   /'
    # Note: ports published by Docker bypass ufw. The stack publishes only 80/443 (Caddy) or
    # 127.0.0.1:8000 (--no-caddy); the database has no published port.
fi

# ---------------------------------------------------------------- 4. security updates
info "4/7 automatic security updates"
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
// smart-campus-robot install.sh: daily package lists + unattended upgrades. The allowed origins are
// the distribution's defaults in 50unattended-upgrades (security updates); Docker's repository is
// not among them, so Docker is never upgraded (and restarted) behind your back.
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
if [[ $HAVE_SYSTEMD -eq 1 ]]; then systemctl enable --now unattended-upgrades >/dev/null 2>&1 || true; fi
note "enabled (/etc/apt/apt.conf.d/20auto-upgrades)"

# ---------------------------------------------------------------- 5. code
info "5/7 code in $TARGET"
if [[ -n "$DEPLOY_KEY" ]]; then
    chmod 600 "$DEPLOY_KEY"
    export GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi
if git -C "$TARGET" rev-parse --git-dir >/dev/null 2>&1; then
    note "already a git checkout ($(git -C "$TARGET" log -1 --format='%h %s' 2>/dev/null || echo 'commit unknown')): kept. To update: deploy/server/update.sh"
elif [[ -e "$TARGET" && -n "$(ls -A "$TARGET" 2>/dev/null)" ]]; then
    if [[ -f "$TARGET/docker-compose.prod.yml" && -f "$TARGET/deploy/server/lib.sh" ]]; then
        warn "$TARGET holds the code but is not a git checkout: kept, but update.sh will not work there"
    else
        die "$TARGET exists, is not empty and is not a checkout of the project: move it away first"
    fi
else
    if [[ "$REPO_URL" == git@* && -z "$DEPLOY_KEY" ]]; then
        warn "an SSH URL without --deploy-key: make one with  ssh-keygen -t ed25519 -N '' -f /root/.ssh/smart-campus-deploy"
        warn "add /root/.ssh/smart-campus-deploy.pub in GitHub (repository Settings > Deploy keys, read only)"
        warn "and run again with --deploy-key /root/.ssh/smart-campus-deploy"
    fi
    git clone --branch "$REF" "$REPO_URL" "$TARGET" ||
        die "git clone of $REPO_URL (branch/tag $REF) failed: check --repo, --ref and the access (deploy key)"
    if [[ -n "$DEPLOY_KEY" ]]; then
        git -C "$TARGET" config core.sshCommand "$GIT_SSH_COMMAND"
    fi
    note "cloned $REPO_URL ($REF): $(git -C "$TARGET" log -1 --format='%h %s')"
fi
# from here on the scripts and settings of $TARGET are used
REPO="$TARGET"
SCRIPTS="$REPO/deploy/server"
ENV_FILE="$REPO/deploy/server/.env"
export SCR_ENV_FILE="$ENV_FILE"
EXAMPLE="$REPO/deploy/server/.env.production.example"
[[ -f "$EXAMPLE" ]] || die "$EXAMPLE not found (is $TARGET at a release with server mode?)"

# ---------------------------------------------------------------- 6. settings (.env)
info "6/7 settings $ENV_FILE"
gen_secret() { openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-48; }
gen_code() {
    # 3 groups of 4 easy-to-type characters (no 0/o, 1/l/i)
    local c
    c="$(head -c 4096 /dev/urandom | LC_ALL=C tr -dc 'abcdefghjkmnpqrstuvwxyz23456789' | cut -c1-12)"
    printf '%s-%s-%s' "${c:0:4}" "${c:4:4}" "${c:8:4}"
}
# the value for KEY=__GENERATE__
gen_for() { if [[ "$1" == SITE_ACCESS_CODE ]]; then gen_code; else gen_secret; fi; }

umask 077
if [[ -f "$ENV_FILE" ]]; then
    note "exists: kept (secrets are not regenerated); adding keys that are missing"
    added=0
    while IFS= read -r line; do
        [[ "$line" =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]] || continue
        key="${BASH_REMATCH[1]}"
        val="${BASH_REMATCH[2]}"
        env_has "$key" && continue
        [[ "$val" == __GENERATE__ ]] && val="$(gen_for "$key")"
        printf '%s=%s\n' "$key" "$val" >>"$ENV_FILE"
        note "added $key"
        added=$((added + 1))
    done <"$EXAMPLE"
    note "$added key(s) added"
else
    tmp="$(mktemp "$(dirname "$ENV_FILE")/.env.XXXXXX")"
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" =~ ^([A-Z][A-Z0-9_]*)=__GENERATE__[[:space:]]*$ ]]; then
            printf '%s=%s\n' "${BASH_REMATCH[1]}" "$(gen_for "${BASH_REMATCH[1]}")"
        else
            printf '%s\n' "$line"
        fi
    done <"$EXAMPLE" >"$tmp"
    mv -f "$tmp" "$ENV_FILE"
    note "created from $(basename "$EXAMPLE") with generated secrets"
fi
chown root:root "$ENV_FILE"
chmod 600 "$ENV_FILE"
if grep -qE '^[A-Z][A-Z0-9_]*=.*__GENERATE__' "$ENV_FILE"; then
    die "$ENV_FILE still holds __GENERATE__ placeholders (a key with a value after the placeholder?): fix it by hand"
fi

if [[ $NO_CADDY -eq 1 ]]; then
    env_set USE_CADDY false
    note "USE_CADDY=false (behind an existing web server; the backend listens on $(env_get BACKEND_BIND))"
fi

# domain (the example holds the placeholder __DOMAIN__ in DOMAIN, PUBLIC_BASE_URL and CORS_ORIGINS)
DOMAIN="$(env_get DOMAIN)"
[[ "$DOMAIN" != *__DOMAIN__* ]] || DOMAIN=""
OLD_DOMAIN="$DOMAIN"
if [[ -n "$DOMAIN_ARG" ]]; then
    DOMAIN="$DOMAIN_ARG"
elif [[ -z "$DOMAIN" && $YES -eq 0 && -t 0 ]]; then
    read -r -p "   Domain name for this server (DNS A record pointing here), empty = use sslip.io: " DOMAIN
    DOMAIN="$(printf '%s' "$DOMAIN" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    [[ -z "$DOMAIN" || "$DOMAIN" =~ $DOMAIN_RE ]] || die "not a host name: $DOMAIN"
fi
if [[ -z "$DOMAIN" ]]; then
    ip="$(curl -4 -fsS --max-time 10 https://api.ipify.org 2>/dev/null ||
        curl -4 -fsS --max-time 10 https://ifconfig.me 2>/dev/null || true)"
    if [[ ! "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
        ip="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+(\.[0-9]+){3}$' | head -n 1 || true)"
        warn "public IPv4 not found online; using the local address ${ip:-?} (may not be reachable from outside)"
    fi
    [[ -n "$ip" ]] || die "no IPv4 address found: pass --domain"
    DOMAIN="${ip//./-}.sslip.io"
    note "no domain given: using $DOMAIN (sslip.io resolves it to $ip)"
fi
env_set DOMAIN "$DOMAIN"
env_set PUBLIC_BASE_URL "https://$DOMAIN"
# CORS_ORIGINS follows the domain unless it was changed by hand to something else
cors="$(env_get CORS_ORIGINS)"
if env_has CORS_ORIGINS && [[ -z "$cors" || "$cors" == *__DOMAIN__* || "$cors" == "https://$OLD_DOMAIN" ]]; then
    env_set CORS_ORIGINS "https://$DOMAIN"
fi
note "DOMAIN=$DOMAIN, PUBLIC_BASE_URL=https://$DOMAIN, CORS_ORIGINS=$(env_get CORS_ORIGINS)"

# OpenAI key (hidden input; never printed or logged; stored only in .env)
current_key_set=1
case "$(env_get OPENAI_API_KEY)" in "" | *__OPENAI_API_KEY__*) current_key_set=0 ;; esac
if [[ $current_key_set -eq 0 || $ASK_KEY -eq 1 ]]; then
    key=""
    if [[ $YES -eq 1 ]]; then
        if [[ -n "${OPENAI_API_KEY_FILE:-}" ]]; then
            [[ -r "$OPENAI_API_KEY_FILE" ]] || die "OPENAI_API_KEY_FILE: cannot read $OPENAI_API_KEY_FILE"
            key="$(tr -d '[:space:]' <"$OPENAI_API_KEY_FILE")"
        fi
    elif [[ -t 0 ]]; then
        read -r -s -p "   OpenAI API key (hidden; empty = none for now): " key
        echo
        key="$(printf '%s' "$key" | tr -d '[:space:]')"
    fi
    if [[ -n "$key" ]]; then
        [[ "$key" =~ ^[A-Za-z0-9_-]+$ ]] || die "the OpenAI key has unexpected characters (not stored)"
        env_set OPENAI_API_KEY "$key"
        note "OpenAI key stored in $ENV_FILE (${#key} characters)"
    else
        [[ $current_key_set -eq 1 ]] || env_set OPENAI_API_KEY ""
        warn "no OpenAI key: questions get the offline reply until you add OPENAI_API_KEY to $ENV_FILE"
        warn "(then: sudo docker compose -p $SCR_PROJECT ... up -d, or simply run install.sh again)"
    fi
    unset key
else
    note "OpenAI key: already set in $ENV_FILE (kept; --ask-openai-key to change it)"
fi
chmod 600 "$ENV_FILE"
PLACEHOLDERS='^[A-Z][A-Z0-9_]*=.*__(GENERATE|DOMAIN|OPENAI_API_KEY)__'
if grep -qE "$PLACEHOLDERS" "$ENV_FILE"; then
    die "$ENV_FILE still holds a placeholder in: $(grep -E "$PLACEHOLDERS" "$ENV_FILE" | cut -d= -f1 | paste -sd' ') (fix it by hand)"
fi

# ---------------------------------------------------------------- 7. start
info "7/7 build and start"
if [[ $NO_START -eq 1 ]]; then
    note "skipped (--no-start). To start later: run install.sh again without --no-start"
else
    docker info >/dev/null 2>&1 || die "the Docker daemon is not running (systemctl start docker)"
    dc build
    dc up -d
    wait_healthy 300 || die "the stack started but the backend is not healthy (see above; sudo bash $SCRIPTS/status.sh)"
fi

URL="https://$DOMAIN"
cat <<MSG

Done.
  Health:        $URL/health
  Admin panel:   $URL/admin
  Visitor pages: $URL/access   (the access code is in $ENV_FILE, key SITE_ACCESS_CODE)
  Settings:      $ENV_FILE  (root only, mode 600; never commit or share it)
  Log:           $LOG  (no secrets)
MSG
if ! use_caddy; then
    echo "  No Caddy: point your web server (TLS) at $(env_get BACKEND_BIND) (docs/DEPLOY.md \"Server mode\")."
fi
# The access code goes to the terminal only, not to the log.
if { : >/dev/tty; } 2>/dev/null; then
    printf '  Access code for the visitor pages (shown once here, not logged): %s\n' "$(env_get SITE_ACCESS_CODE)" >/dev/tty
fi
cat <<MSG

Next steps:
  1. copy the data bundle from the laptop:  scp backups/smart-campus-bundle-*.tar.gz root@$DOMAIN:/root/
     then:  sudo bash $SCRIPTS/restore-bundle.sh /root/smart-campus-bundle-<date>.tar.gz
  2. an admin login:  sudo bash $SCRIPTS/create-admin.sh <name>
  3. the robot: BACKEND_URL=wss://$DOMAIN/ws/robot and ROBOT_TOKEN from $ENV_FILE (docs/DEPLOY.md)
  4. check:  sudo bash $SCRIPTS/status.sh
MSG
