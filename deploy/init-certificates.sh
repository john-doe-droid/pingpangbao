#!/usr/bin/env sh
set -eu

if [ "$#" -ne 2 ]; then
    echo "用法：sh deploy/init-certificates.sh <裸域名> <证书通知邮箱>" >&2
    exit 2
fi

domain=$1
email=$2
app_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
www_dir="$app_dir/deploy/certbot/www"
conf_dir="$app_dir/deploy/certbot/conf"

case "$domain" in
    ""|.*|*.|*..*|*[!A-Za-z0-9.-]*)
        echo "域名必须是不带协议和路径的裸域名。" >&2
        exit 2
        ;;
esac

configured_domain=$(
    sed -n 's/^DOMAIN=//p' "$app_dir/.env" 2>/dev/null \
        | tail -n 1 | tr -d '\r'
)
if [ -n "$configured_domain" ] && [ "$configured_domain" != "$domain" ]; then
    echo ".env 中的 DOMAIN=$configured_domain 与参数 $domain 不一致。" >&2
    exit 2
fi

mkdir -p "$www_dir" "$conf_dir"

cleanup() {
    docker rm -f pingpong-acme-bootstrap >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker run -d --rm --name pingpong-acme-bootstrap \
    -p 80:80 \
    -v "$app_dir/deploy/nginx/acme-bootstrap.conf:/etc/nginx/conf.d/default.conf:ro" \
    -v "$www_dir:/var/www/certbot:ro" \
    nginx:1.27-alpine

# 给临时 Nginx 少量启动时间；若端口被占用或容器退出，Certbot 会明确报错。
sleep 2
docker exec pingpong-acme-bootstrap nginx -t

docker run --rm \
    -v "$conf_dir:/etc/letsencrypt" \
    -v "$www_dir:/var/www/certbot" \
    certbot/certbot certonly --webroot -w /var/www/certbot \
    -d "$domain" --email "$email" --agree-tos --no-eff-email

docker run --rm \
    -v "$conf_dir:/etc/letsencrypt:ro" \
    --entrypoint sh certbot/certbot -c \
    "test -f /etc/letsencrypt/live/$domain/fullchain.pem && \
     test -f /etc/letsencrypt/live/$domain/privkey.pem"
echo "证书已签发：$conf_dir/live/$domain"
