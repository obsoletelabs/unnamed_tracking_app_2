#!/bin/sh
set -eu

source="${1:-/etc/nginx/ready.conf}"
output="${2:-/etc/nginx/ready.conf}"
enabled="${NGINX_TLS_ENABLED:-false}"
redirect="${NGINX_TLS_REDIRECT_HTTP:-false}"
redirect_port="${NGINX_TLS_REDIRECT_PORT:-443}"
cert="${NGINX_TLS_CERTIFICATE:-}"
key="${NGINX_TLS_PRIVATE_KEY:-}"
realip_header="${NGINX_REALIP_HEADER:-X-Forwarded-For}"
default_trusted_proxies="127.0.0.1/32 ::1/128"
if [ "${NGINX_REALIP_TRUSTED_PROXIES+x}" = x ]; then
  trusted_proxies="$NGINX_REALIP_TRUSTED_PROXIES"
else
  trusted_proxies="$default_trusted_proxies"
fi
generated_dir="/run/unnamed-tracking/tls"
tmp_output="${output}.tmp.$$"
realip_tmp="${output}.realip.$$"

cleanup() {
  rm -f "$tmp_output" "${tmp_output}.base" "${tmp_output}.redirect" "$realip_tmp"
}
trap cleanup EXIT INT TERM

case "$enabled" in
  true|TRUE|1|yes|YES) enabled=true ;;
  false|FALSE|0|no|NO|"") enabled=false ;;
  *) printf '%s\n' "Invalid NGINX_TLS_ENABLED value." >&2; exit 1 ;;
esac
case "$redirect" in
  true|TRUE|1|yes|YES) redirect=true ;;
  false|FALSE|0|no|NO|"") redirect=false ;;
  *) printf '%s\n' "Invalid NGINX_TLS_REDIRECT_HTTP value." >&2; exit 1 ;;
esac
# Leading zeros are dropped (sh would read them as octal); 1-65535 only.
redirect_port="$(printf '%s' "$redirect_port" | sed 's/^0*//')"
case "$redirect_port" in
  ''|*[!0-9]*|??????*) printf '%s\n' "Invalid NGINX_TLS_REDIRECT_PORT value; use a port number." >&2; exit 1 ;;
esac
if [ "$redirect_port" -gt 65535 ]; then
  printf '%s\n' "Invalid NGINX_TLS_REDIRECT_PORT value; use a port number." >&2
  exit 1
fi
if [ "$redirect" = true ] && [ "$enabled" != true ]; then
  printf '%s\n' "HTTP redirection requires TLS to be enabled." >&2
  exit 1
fi
for tls_path in "$cert" "$key"; do
  case "$tls_path" in
    "") ;;
    /*) case "$tls_path" in *[!A-Za-z0-9_./-]*) printf '%s\n' "Invalid TLS file path." >&2; exit 1 ;; esac ;;
    *) printf '%s\n' "TLS file paths must be absolute." >&2; exit 1 ;;
  esac
done

case "$realip_header" in
  ''|*[!A-Za-z0-9_-]*)
    printf '%s\n' "Invalid NGINX_REALIP_HEADER value; use an HTTP header name." >&2
    exit 1
    ;;
esac

for proxy in $trusted_proxies; do
  case "$proxy" in
    unix:|*[!0-9A-Fa-f:./]*)
      if [ "$proxy" != "unix:" ]; then
        printf '%s\n' "Invalid NGINX_REALIP_TRUSTED_PROXIES entry: $proxy" >&2
        exit 1
      fi
      ;;
  esac
done

{
  printf '%s\n' "    # Trusted proxy configuration rendered from NGINX_REALIP_* environment."
  printf '    real_ip_header %s;\n' "$realip_header"
  printf '%s\n' "    real_ip_recursive on;"
  for proxy in $trusted_proxies; do
    printf '    set_real_ip_from %s;\n' "$proxy"
  done
  printf '%s\n' "    # X-Forwarded-Proto is honoured only from those same trusted proxies."
  printf '%s\n' "    geo \$realip_remote_addr \$from_trusted_proxy {"
  printf '%s\n' "        default 0;"
  for proxy in $trusted_proxies; do
    [ "$proxy" = "unix:" ] || printf '        %s 1;\n' "$proxy"
  done
  printf '%s\n' "    }"
  printf '%s\n' "    map \"\$from_trusted_proxy:\$http_x_forwarded_proto\" \$forwarded_proto {"
  printf '%s\n' "        default \$scheme;"
  printf '%s\n' "        \"1:https\" https;"
  printf '%s\n' "        \"1:http\" http;"
  printf '%s\n' "    }"
} > "$realip_tmp"

awk -v snippet="$realip_tmp" '
  BEGIN { inserted = 0 }
  !inserted && $0 ~ /^[[:space:]]*server[[:space:]]*\{/ {
    while ((getline line < snippet) > 0) print line
    close(snippet)
    inserted = 1
  }
  { print }
' "$source" > "${tmp_output}.redirect"

# Redirect to the HTTPS port clients reach, which may be a published port other than 443.
if [ "$redirect_port" = 443 ]; then
  cat "${tmp_output}.redirect" > "${tmp_output}.base"
else
  sed -e "s#return 301 https://\$host\$request_uri;#return 301 https://\$host:$redirect_port\$request_uri;#" \
    "${tmp_output}.redirect" > "${tmp_output}.base"
fi
rm -f "${tmp_output}.redirect"

if [ "$enabled" = "true" ]; then
  default_cert="/etc/nginx/tls/tls.crt"
  default_key="/etc/nginx/tls/tls.key"

  if [ -z "$cert" ] && [ -z "$key" ]; then
    if [ -s "$default_cert" ] && [ -s "$default_key" ]; then
      cert="$default_cert"
      key="$default_key"
    else
      cert="$generated_dir/tls.crt"
      key="$generated_dir/tls.key"
    fi
  elif [ -z "$cert" ] || [ -z "$key" ]; then
    printf '%s\n' "NGINX_TLS_CERTIFICATE and NGINX_TLS_PRIVATE_KEY must be supplied together." >&2
    exit 1
  elif [ "$cert" = "$default_cert" ] && [ "$key" = "$default_key" ] &&
       { [ ! -s "$cert" ] || [ ! -s "$key" ]; }; then
    cert="$generated_dir/tls.crt"
    key="$generated_dir/tls.key"
  fi

  if [ "$cert" = "$generated_dir/tls.crt" ] && [ "$key" = "$generated_dir/tls.key" ] &&
     { [ ! -s "$cert" ] || [ ! -s "$key" ]; }; then
    printf '%s\n' "No usable TLS certificate/private key supplied; generating a self-signed certificate for localhost." >&2
    mkdir -p "$generated_dir"
    umask 077
    openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
      -keyout "$key" -out "$cert" \
      -subj "/CN=localhost" \
      -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" >/dev/null 2>&1
    chmod 0644 "$cert"
    chmod 0600 "$key"
  fi
  if [ ! -f "$cert" ]; then printf '%s\n' "TLS certificate file is missing: $cert" >&2; exit 1; fi
  if [ ! -f "$key" ]; then printf '%s\n' "TLS private key file is missing: $key" >&2; exit 1; fi
  if [ ! -r "$cert" ]; then printf '%s\n' "TLS certificate is not readable: $cert" >&2; exit 1; fi
  if [ ! -r "$key" ]; then printf '%s\n' "TLS private key is not readable: $key" >&2; exit 1; fi
  sed -e "s#ssl_certificate /etc/nginx/tls/tls.crt;#ssl_certificate $cert;#" \
      -e "s#ssl_certificate_key /etc/nginx/tls/tls.key;#ssl_certificate_key $key;#" \
      "${tmp_output}.base" > "$tmp_output"
else
  cat "${tmp_output}.base" > "$tmp_output"
fi

rm -f "${tmp_output}.base" "$realip_tmp"
mv "$tmp_output" "$output"
trap - EXIT INT TERM
