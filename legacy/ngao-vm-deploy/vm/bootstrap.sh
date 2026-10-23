#!/usr/bin/env bash
# One-time VM setup, run with sudo (the deploy runs it when Docker is missing):
#   * Docker Engine + Compose plugin (Oracle Linux 8/9 or Ubuntu 22.04/24.04, x86_64 or aarch64)
#   * host firewall: allow 80/tcp, 443/tcp and 443/udp (HTTPS for Caddy)
#   * /opt/ngao owned by the deploy user, who joins the docker group
# The OCI security list / NSG of the VM's subnet must also allow ingress on 80 and 443 (console step).
#
#   sudo bash bootstrap.sh <deploy-user>
set -euo pipefail

deploy_user="${1:-${SUDO_USER:-}}"
if [[ -z "$deploy_user" || "$deploy_user" == "root" ]]; then
  echo "usage: sudo bash bootstrap.sh <deploy-user>" >&2
  exit 2
fi
if [[ "$(id -u)" -ne 0 ]]; then
  echo "bootstrap.sh must run as root (sudo)" >&2
  exit 2
fi

# shellcheck disable=SC1091
. /etc/os-release

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    echo "Docker and Compose already installed: $(docker --version)"
    return
  fi
  case "$ID" in
    ol | rhel | centos | rocky | almalinux)
      dnf -y install dnf-plugins-core
      dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
      dnf -y remove podman-docker buildah runc 2>/dev/null || true
      dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
      ;;
    ubuntu | debian)
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -y
      apt-get install -y ca-certificates curl
      install -m 0755 -d /etc/apt/keyrings
      curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
      chmod a+r /etc/apt/keyrings/docker.asc
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$ID ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list
      apt-get update -y
      apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
      ;;
    *)
      echo "Unsupported OS: $ID. Install Docker Engine and the Compose plugin by hand." >&2
      exit 1
      ;;
  esac
  systemctl enable --now docker
}

open_firewall() {
  if systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-service=http --add-service=https --add-port=443/udp
    firewall-cmd --reload
    echo "firewalld: http/https open"
  elif command -v iptables >/dev/null 2>&1; then
    # OCI Ubuntu images end INPUT with a REJECT rule; insert the ACCEPTs above it and persist.
    for rule in "-p tcp --dport 80" "-p tcp --dport 443" "-p udp --dport 443"; do
      # shellcheck disable=SC2086
      iptables -C INPUT -m state --state NEW $rule -j ACCEPT 2>/dev/null \
        || iptables -I INPUT 5 -m state --state NEW $rule -j ACCEPT
    done
    if ! command -v netfilter-persistent >/dev/null 2>&1; then
      DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent >/dev/null 2>&1 || true
    fi
    if command -v netfilter-persistent >/dev/null 2>&1; then
      netfilter-persistent save
    else
      echo "warning: rules are open now but will not survive a reboot (no netfilter-persistent)"
    fi
    echo "iptables: 80/443 open"
  fi
}

install_docker
open_firewall
usermod -aG docker "$deploy_user"
install -d -m 0750 -o "$deploy_user" -g "$deploy_user" /opt/ngao
echo "bootstrap done: docker $(docker --version | awk '{print $3}' | tr -d ,), compose $(docker compose version --short)"
