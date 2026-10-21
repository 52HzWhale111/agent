#!/bin/sh
# Run as root inside the dedicated Debian WSL distribution.
set -eu
test "$(id -u)" -eq 0
. /etc/os-release
test "$ID" = debian
case "$VERSION_CODENAME" in bookworm|trixie) ;; *) echo 'Unsupported Debian release' >&2; exit 1 ;; esac
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL --retry 3 https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: $VERSION_CODENAME
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
service docker start
docker info --format 'Docker {{.ServerVersion}} / {{.OSType}}'
