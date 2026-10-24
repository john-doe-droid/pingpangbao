# Azure 部署采购清单

## 结论

本项目已经包含 PostgreSQL、Gunicorn、Nginx 和定时任务，**推荐购买一台 Ubuntu
Linux 虚拟机**，继续使用现有 Docker Compose。不要为当前规模购买 AKS、Azure SQL、
Application Gateway 或 Redis，这些都会增加费用和运维复杂度。

适合规模：几十名社团成员、单机运行、非高可用场景。

## 必须购买或创建

| Azure 资源 | 推荐规格 | 作用 |
|---|---|---|
| 虚拟机 | `Standard_B2als_v2`，2 vCPU / 4 GiB，Ubuntu 24.04 LTS | 运行 Docker、PostgreSQL、Django、Gunicorn、Nginx |
| 操作系统盘 | Standard SSD，64 GiB，LRS | Ubuntu、Docker 镜像和系统日志 |
| 数据盘 | Standard SSD，64 GiB，LRS | 数据库与附件统一挂载到 `/srv/pingpong-data` |
| 公共 IP | Standard、Static IPv4 | 固定公网入口；不要选已退役的 Basic SKU |
| 虚拟网络与 NSG | 默认 VNet/子网即可 | 只开放 22（限本人 IP）、80、443 |

`Standard_B2als_v2` 在该区域不可用时，退回 `Standard_B2s`（2 vCPU / 4 GiB）。
`B1s` 只有 1 GiB 内存，不建议承载 PostgreSQL 与 3 个 Gunicorn worker。

## 不需要单独购买

- Nginx、PostgreSQL、Gunicorn：已经在 Docker Compose 中，不购买 Azure 托管服务。
- TLS 证书：继续使用 Let's Encrypt，免费。
- 域名解析：可以使用 Azure DNS，也可以继续使用域名注册商提供的 DNS。
- 堡垒机服务：单人维护优先使用 NSG 把 SSH 限制到自己的公网 IP，不必先购买 Bastion。

## 域名与 DNS

域名本身不是 Azure 虚拟机附赠品，需要从域名注册商购买。建议把 A 记录指向
Standard Public IP，再在 `.env` 中填写裸域名：

```ini
DOMAIN=pingpong.example.com
DJANGO_ALLOWED_HOSTS=pingpong.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://pingpong.example.com
```

如果使用 Azure DNS，Public Zone 约为 `US$0.50/月`，查询量很小时查询费通常不到
几美分。使用域名注册商自带 DNS 可以省掉这部分。

## 备份

本项目的 `deploy/backup.sh` 会在服务器本地生成数据库和附件备份，但**本地备份仍然与
服务器在同一故障域**。至少再选一种：

- 最低成本：把 `/srv/backup` 定时同步到 Azure Blob Storage。
- 更省心：启用 Azure Backup 备份整台虚拟机或数据盘。

无论选哪种，上线前都要实际恢复一次。只看“备份成功”不能证明备份可用。

## 2026-10-24 的 East Asia 价格参考

以下为 Azure Retail Prices API 的按量付费美元价格，供预算判断；实际账单受汇率、
税、区域、账户协议和后续调价影响，购买前请用 Azure 定价计算器复核。

| 项目 | 估算 |
|---|---:|
| `Standard_B2als_v2` Linux | `US$0.0526/小时`，约 `US$38.40/月` |
| 两块 64 GiB Standard SSD LRS | 约 `US$9.60/月` |
| Standard Static IPv4 | `US$0.005/小时`，约 `US$3.65/月` |
| Azure DNS Public Zone（可选） | 约 `US$0.50/月` |

基础合计约 **US$51.65/月**，不含域名、备份存储、公网流量和税费。一般社团网站
的出口流量很小，但仍建议在 Azure 中设置预算提醒。

Azure Retail Prices API：
<https://prices.azure.com/api/retail/prices>

## 网络防火墙

NSG 入站规则建议：

| 端口 | 来源 | 用途 |
|---|---|---|
| 22 | 仅管理员固定公网 IP | SSH 运维 |
| 80 | 任意 | HTTP 跳转到 HTTPS 与 ACME 验证 |
| 443 | 任意 | 网站 HTTPS |

不要开放 `5432`、`8000`。它们只应存在于 Docker 内部网络。

## 如果使用的是 Azure China

如果订阅来自由世纪互联运营的 Azure China，产品规格、价格和可用区与本文不同：

- 选择 China East 2 或 China North 3 中当前有 `Bsv2/Basv2` 容量的区域。
- 中国大陆服务器绑定域名通常要求完成 ICP 备案。
- 价格请查看 Azure China 定价页，不能直接套用本文的 Global 价格。

## 购买后

按 [`DEPLOY.md`](DEPLOY.md) 完成数据盘挂载、生产 `.env`、证书、启动和恢复演练。
不要直接把本地开发 `.env` 上传到服务器。
