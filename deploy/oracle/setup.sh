#!/usr/bin/env bash
# roomscan API on an Oracle Cloud "Always Free" Ampere (ARM) VM, Ubuntu 22.04 / 24.04.
#
#   ssh -i <key> ubuntu@<public-ip> 'bash -s' < deploy/oracle/setup.sh
#
# Installs Docker, builds server/Dockerfile natively on ARM, runs it with --restart
# unless-stopped (survives reboots), keeps projects and results on the VM disk
# (/opt/roomscan-data), and puts Caddy in front for HTTPS on https://<ip-with-dashes>.sslip.io
# (a free hostname that resolves to the IP; Caddy gets the Let's Encrypt certificate itself).
# Re-run it to update: it pulls main, rebuilds and replaces the container.
#
# Settings live in /etc/roomscan.env on the VM (created on the first run; edit it, then re-run).
# Email: add ROOMSCAN_SMTP_USER / ROOMSCAN_SMTP_PASSWORD there by hand, never in this repo.
set -euo pipefail

REPO=${REPO:-https://github.com/jaiChauhan009/roomscan.git}
SRC=/opt/roomscan
DATA=/opt/roomscan-data
IP=$(curl -fsS https://ifconfig.me || curl -fsS https://api.ipify.org)
HOST=${HOST:-$(echo "$IP" | tr . -).sslip.io}

echo "== packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io git iptables-persistent >/dev/null
sudo systemctl enable --now docker

echo "== firewall: open 80 and 443 (Oracle's Ubuntu image rejects everything but 22)"
for p in 80 443; do
  sudo iptables -C INPUT -p tcp --dport $p -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 5 -p tcp --dport $p -j ACCEPT
done
sudo netfilter-persistent save >/dev/null

echo "== source"
if [ -d $SRC/.git ]; then sudo git -C $SRC pull -q --ff-only; else sudo git clone -q --depth 1 "$REPO" $SRC; fi

echo "== settings"
if [ ! -f /etc/roomscan.env ]; then
  sudo tee /etc/roomscan.env >/dev/null <<ENV
ROOMSCAN_CORS=*
ROOMSCAN_RESULT_TTL_DAYS=7
ROOMSCAN_API_URL=https://$HOST
ROOMSCAN_WEB_URL=https://roomscan.vercel.app
# ROOMSCAN_SMTP_USER=you@gmail.com
# ROOMSCAN_SMTP_PASSWORD=your-app-password
ENV
  sudo chmod 600 /etc/roomscan.env
fi

echo "== build (first time ~15-25 min on 4 ARM cores)"
sudo docker build -q -f $SRC/server/Dockerfile -t roomscan $SRC

echo "== run"
sudo mkdir -p $DATA && sudo chown 1000:1000 $DATA
sudo docker rm -f roomscan >/dev/null 2>&1 || true
sudo docker run -d --name roomscan --restart unless-stopped -p 127.0.0.1:7860:7860 \
  -v $DATA:/data --env-file /etc/roomscan.env roomscan >/dev/null

echo "== https (Caddy) for $HOST"
sudo mkdir -p /opt/caddy
sudo tee /opt/caddy/Caddyfile >/dev/null <<CADDY
$HOST {
	request_body {
		max_size 2GB
	}
	reverse_proxy 127.0.0.1:7860 {
		transport http {
			read_timeout 30m
			write_timeout 30m
		}
	}
}
CADDY
sudo docker rm -f caddy >/dev/null 2>&1 || true
sudo docker run -d --name caddy --restart unless-stopped --network host \
  -v /opt/caddy/Caddyfile:/etc/caddy/Caddyfile -v caddy_data:/data caddy:2 >/dev/null

echo "== waiting for the API"
for i in $(seq 1 60); do curl -fsS http://127.0.0.1:7860/api/health && break; sleep 5; done
echo
echo "API: https://$HOST   (health: https://$HOST/api/health)"
