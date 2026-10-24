#!/usr/bin/env sh
set -eu

app_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${PINGPONG_BACKUP_DIR:-/srv/backup}
retention_days=${BACKUP_RETENTION_DAYS:-30}
stamp=$(date +%Y%m%d_%H%M%S)
db_target="$backup_dir/db_$stamp.dump"
media_target="$backup_dir/media_$stamp.tar.gz"
db_temp="$db_target.partial"
media_temp="$media_target.partial"

data_dir=$(
    sed -n 's/^PINGPONG_DATA_DIR=//p' "$app_dir/.env" 2>/dev/null \
        | tail -n 1 | tr -d '\r'
)
data_dir=${data_dir:-./data}
case "$data_dir" in
    /*) ;;
    *) data_dir="$app_dir/$data_dir" ;;
esac

cleanup() {
    rm -f "$db_temp" "$media_temp"
}
trap cleanup EXIT INT TERM

mkdir -p "$backup_dir"
if [ ! -w "$backup_dir" ]; then
    echo "备份目录不可写：$backup_dir。请把属主设为运行 cron 的用户。" >&2
    exit 1
fi
if [ ! -d "$data_dir/private_media" ]; then
    echo "附件目录不存在：$data_dir/private_media" >&2
    exit 1
fi

umask 077
cd "$app_dir"

docker compose --env-file .env exec -T db sh -c \
    'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$db_temp"
if [ ! -s "$db_temp" ]; then
    echo "数据库备份为空，已中止。" >&2
    exit 1
fi

tar -czf "$media_temp" -C "$data_dir" private_media
mv "$db_temp" "$db_target"
mv "$media_temp" "$media_target"

find "$backup_dir" -type f \
    \( -name 'db_*.dump' -o -name 'media_*.tar.gz' \) \
    -mtime "+$retention_days" -delete

echo "备份完成："
echo "  $db_target"
echo "  $media_target"
