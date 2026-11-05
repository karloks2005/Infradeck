#!/bin/sh
# Decides whose X-Forwarded-For nginx believes, from TRUSTED_PROXIES: the addresses or CIDR ranges of the reverse
# proxy in front of the dashboard (Traefik, Caddy, nginx, HAProxy, a tunnel connector...), separated by commas.
# Empty (the default): nobody is trusted and every request counts as coming from the address that connected.
#
# Runs from the nginx image's /docker-entrypoint.d before nginx starts. An entry that isn't an IP address or a
# CIDR range stops the container instead of being written into the configuration. The optional argument is the
# file to write (used by the tests).
set -euf

out="${1:-/etc/nginx/conf.d/trusted-proxies.conf}"
ipv4='^([0-9]{1,3}\.){3}[0-9]{1,3}(/[0-9]{1,2})?$'
ipv6='^[0-9A-Fa-f.]*:[0-9A-Fa-f:.]*(/[0-9]{1,3})?$'

entries=""
for entry in $(printf '%s' "${TRUSTED_PROXIES:-}" | tr ',' ' '); do
    if ! printf '%s' "$entry" | grep -Eq "$ipv4|$ipv6"; then
        echo "TRUSTED_PROXIES: '$entry' is not an IP address or CIDR range" >&2
        exit 1
    fi
    entries="$entries $entry"
done

{
    echo "# Generated from TRUSTED_PROXIES when the container starts; edit that setting instead."
    for entry in $entries; do
        echo "set_real_ip_from $entry;"
    done
    if [ -n "$entries" ]; then
        echo "real_ip_header X-Forwarded-For;"
        echo "real_ip_recursive on;"
    fi
} > "$out"

echo "Trusting X-Forwarded-For from:${entries:- nobody}"
