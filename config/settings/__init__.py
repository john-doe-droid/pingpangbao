"""
配置分层的公共入口。

通过 DJANGO_SETTINGS_MODULE 选择具体配置：
    config.settings.dev    —— 本地开发
    config.settings.prod   —— 生产部署

默认（未设置环境变量时）指向 dev，避免本地开发时因忘记设置而崩溃；
生产部署必须在环境变量里显式指定 prod，Docker/systemd 配置已固定这一点。
"""
