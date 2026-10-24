#!/usr/bin/env sh
set -eu

app_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
www_dir="$app_dir/deploy/certbot/www"
conf_dir="$app_dir/deploy/certbot/conf"

docker run --rm \
    -v "$conf_dir:/etc/letsencrypt" \
    -v "$www_dir:/var/www/certbot" \
    certbot/certbot renew --webroot -w /var/www/certbot --quiet

# 读取挂载目录中的新证书，不中断现有连接。
cd "$app_dir"
docker compose --env-file .env exec -T nginx nginx -s reload
echo "证书续期检查完成，Nginx 已重新加载。"
