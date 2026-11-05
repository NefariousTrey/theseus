#!/usr/bin/env bash
# Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License. Made by Trey with Grok.
# One-time setup of a fresh Vultr Cloud Compute VM (Ubuntu 24.04). Run as root from the unpacked repo:
#   sudo THESEUS_DOMAIN=203-0-113-7.sslip.io bash deploy/setup-vultr.sh
# Then put the keys in /etc/theseus/env (see deploy/env.example) and: systemctl restart theseus-web
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DOMAIN="${THESEUS_DOMAIN:?set THESEUS_DOMAIN, e.g. 203-0-113-7.sslip.io (your VM IP with dashes)}"
NODE_VER="${NODE_VER:-v22.20.0}"

echo "== packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q bubblewrap python3 git caddy ufw xz-utils curl ca-certificates apparmor-utils

echo "== node $NODE_VER (official tarball, checksum verified)"
if ! command -v node >/dev/null || [[ "$(node -v)" != "$NODE_VER" ]]; then
  tmp=$(mktemp -d)
  curl -fsSLo "$tmp/node.tar.xz" "https://nodejs.org/dist/$NODE_VER/node-$NODE_VER-linux-x64.tar.xz"
  curl -fsSLo "$tmp/SHASUMS256.txt" "https://nodejs.org/dist/$NODE_VER/SHASUMS256.txt"
  (cd "$tmp" && grep " node-$NODE_VER-linux-x64.tar.xz\$" SHASUMS256.txt | sed 's/node-.*$/node.tar.xz/' | sha256sum -c -)
  tar -xJf "$tmp/node.tar.xz" -C /usr/local --strip-components=1
  rm -rf "$tmp"
fi
node -v

echo "== let bwrap make user namespaces under Ubuntu's AppArmor rule (bwrap only, nothing else)"
cat > /etc/apparmor.d/bwrap <<'AA'
abi <abi/4.0>,
include <tunables/global>
profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap>
}
AA
apparmor_parser -r /etc/apparmor.d/bwrap || true

echo "== bubblewrap 0.11 (Ubuntu 24.04 ships 0.9, which has no --overlay-src for node_modules overlays)"
BW_VER=0.11.0
BW_SHA=988fd6b232dafa04b8b8198723efeaccdb3c6aa9c1c7936219d5791a8b7a8646  # signed by Simon McVittie, checked 2026-11-04
if [[ "$(/usr/local/bin/bwrap --version 2>/dev/null)" != "bubblewrap $BW_VER" ]]; then
  apt-get install -y -q meson ninja-build gcc pkg-config libcap-dev
  tmp=$(mktemp -d)
  curl -fsSLo "$tmp/bw.tar.xz" "https://github.com/containers/bubblewrap/releases/download/v$BW_VER/bubblewrap-$BW_VER.tar.xz"
  echo "$BW_SHA  $tmp/bw.tar.xz" | sha256sum -c -
  tar -xJf "$tmp/bw.tar.xz" -C "$tmp"
  (cd "$tmp/bubblewrap-$BW_VER" && meson setup _b --prefix=/usr/local -Dman=disabled -Dtests=false \
     -Dbash_completion=disabled -Dzsh_completion=disabled -Dselinux=disabled && ninja -C _b)
  install -m 755 "$tmp/bubblewrap-$BW_VER/_b/bwrap" /usr/local/bin/bwrap
  rm -rf "$tmp"
fi
cat > /etc/apparmor.d/bwrap-local <<'AA'
abi <abi/4.0>,
include <tunables/global>
profile bwrap-local /usr/local/bin/bwrap flags=(unconfined) {
  userns,
}
AA
apparmor_parser -r /etc/apparmor.d/bwrap-local || true

echo "== swap (2 GB) so the small plan stays up; lockbox jobs still get MemorySwapMax=0"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== service user, code, data"
id theseus >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/theseus --shell /usr/sbin/nologin theseus
install -d -o theseus -g theseus -m 750 /var/lib/theseus
install -d -m 755 /opt/theseus
cp -a "$SRC/." /opt/theseus/
chown -R root:root /opt/theseus
install -d -o root -g theseus -m 750 /etc/theseus
if [[ ! -f /etc/theseus/env ]]; then
  install -o root -g root -m 600 "$SRC/deploy/env.example" /etc/theseus/env
  tok=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')
  sed -i "s|^THESEUS_ADMIN_TOKEN=.*|THESEUS_ADMIN_TOKEN=$tok|" /etc/theseus/env
  echo "admin token written to /etc/theseus/env (root only, 0600; systemd reads it before dropping to the theseus user)"
fi
# a user systemd manager for the cgroup memory and process caps (systemd-run --user --scope)
loginctl enable-linger theseus
UID_T=$(id -u theseus)
sed "s|@UID@|$UID_T|g" "$SRC/deploy/theseus-web.service" > /etc/systemd/system/theseus-web.service

echo "== caddy (TLS from Let's Encrypt for $DOMAIN)"
sed "s|@DOMAIN@|$DOMAIN|g" "$SRC/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy

echo "== firewall: ssh, http, https only"
ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw --force enable >/dev/null

systemctl daemon-reload
systemctl enable --now theseus-web
sleep 3
echo "== doctor (as the service user)"
sudo -u theseus XDG_RUNTIME_DIR=/run/user/$UID_T timeout 120 python3 /opt/theseus/theseus.py doctor || true
echo "== containment check"
sudo -u theseus XDG_RUNTIME_DIR=/run/user/$UID_T timeout 180 python3 /opt/theseus/theseus.py contain || true
echo "Done. Open https://$DOMAIN"
