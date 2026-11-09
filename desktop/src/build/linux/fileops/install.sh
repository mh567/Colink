#!/bin/sh
set -eu

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

if [ "$(id -u)" != 0 ]; then
  printf '%s\n' 'Run this installer with sudo.' >&2
  exit 1
fi
if [ "$#" -ne 1 ]; then
  printf '%s\n' 'Usage: sudo ./fileops/install.sh https://colink.example.com' >&2
  exit 1
fi

package_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
cd -- "$package_dir"
for package_file in colink-fileopsd colink-fileops.service org.colink.FileOperations1.conf org.colink.fileops.policy; do
  if [ ! -f "$package_file" ] || [ -L "$package_file" ]; then
    printf '%s\n' 'Extract a complete Colink Linux desktop package and try again.' >&2
    exit 1
  fi
done
if [ ! -d /run/systemd/system ] || ! command -v pkaction >/dev/null 2>&1; then
  printf '%s\n' 'Install systemd and Polkit, then run this installer again.' >&2
  exit 1
fi
if ! getent passwd colink-fileops >/dev/null; then
  useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin colink-fileops
fi
if ! getent passwd colink-fileops | awk -F: '$3 != 0 && $6 == "/nonexistent" && $7 == "/usr/sbin/nologin" {ok=1} END {exit !ok}'; then
  printf '%s\n' 'Reserve the colink-fileops system account for the managed file service.' >&2
  exit 1
fi

install -D -o root -g root -m 0755 colink-fileopsd /usr/local/libexec/colink-fileopsd
server_url=$(runuser -u colink-fileops -- /usr/local/libexec/colink-fileopsd --check-server-url "$1")
install -D -o root -g root -m 0644 colink-fileops.service /etc/systemd/system/colink-fileops.service
install -D -o root -g root -m 0644 org.colink.FileOperations1.conf /etc/dbus-1/system.d/org.colink.FileOperations1.conf
install -D -o root -g root -m 0644 org.colink.fileops.policy /usr/share/polkit-1/actions/org.colink.fileops.policy
config_dir=/etc/colink-fileops
if [ -L "$config_dir" ]; then
  printf '%s\n' 'Use a system directory at /etc/colink-fileops.' >&2
  exit 1
fi
install -d -o root -g root -m 0755 "$config_dir"
config_tmp=$(mktemp "$config_dir/.server-url.XXXXXX")
trap 'rm -f -- "$config_tmp"' EXIT
printf '%s\n' "$server_url" > "$config_tmp"
chmod 0644 "$config_tmp"
mv -fT -- "$config_tmp" "$config_dir/server-url"
busctl --system call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus ReloadConfig
systemctl daemon-reload
systemctl enable colink-fileops.service
systemctl restart colink-fileops.service
printf '%s\n' 'Open Colink and select the managed directory for overwrite and delete tasks.'
