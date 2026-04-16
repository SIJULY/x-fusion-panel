import requests
from nicegui import run

from app.core.state import ADMIN_CONFIG


class CloudflareHandler:
    def __init__(self):
        self.token = ADMIN_CONFIG.get('cf_api_token', '')
        self.email = ADMIN_CONFIG.get('cf_email', '')
        self.root_domain = ADMIN_CONFIG.get('cf_root_domain', '')
        self.base_url = "https://api.cloudflare.com/client/v4"

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.email and "global" in self.token.lower():
            h["X-Auth-Email"] = self.email
            h["X-Auth-Key"] = self.token
        else:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def get_zone_id(self, domain_name=None):
        target = self.root_domain
        if domain_name:
            if self.root_domain and domain_name.endswith(self.root_domain):
                target = self.root_domain
            else:
                parts = domain_name.split('.')
                if len(parts) >= 2:
                    target = f"{parts[-2]}.{parts[-1]}"

        url = f"{self.base_url}/zones?name={target}"
        try:
            r = requests.get(url, headers=self._headers(), timeout=10)
            data = r.json()
            if data.get('success') and len(data['result']) > 0:
                return data['result'][0]['id'], None
            return None, f"未找到 Zone: {target}"
        except Exception as e:
            return None, str(e)

    def set_ssl_flexible(self, zone_id):
        url = f"{self.base_url}/zones/{zone_id}/settings/ssl"
        try:
            payload = {"value": "flexible"}
            r = requests.patch(url, headers=self._headers(), json=payload, timeout=10)
            if r.json().get('success'):
                return True, "SSL 已强制设为 Flexible"
            return True, "SSL 设置指令已发送"
        except Exception as e:
            return False, str(e)

    async def auto_configure(self, ip, sub_prefix):
        if not self.token:
            return False, "未配置 API Token"

        def _task():
            zone_id, err = self.get_zone_id()
            if not zone_id:
                return False, err

            self.set_ssl_flexible(zone_id)

            full_domain = f"{sub_prefix}.{self.root_domain}"
            url = f"{self.base_url}/zones/{zone_id}/dns_records"
            payload = {"type": "A", "name": full_domain, "content": ip, "ttl": 1, "proxied": True}
            try:
                r = requests.post(url, headers=self._headers(), json=payload, timeout=10)
                if r.json().get('success'):
                    return True, f"解析成功: {full_domain}"
                else:
                    return False, f"CF API 报错: {r.text}"
            except Exception as e:
                return False, str(e)

        return await run.io_bound(_task)

    async def delete_record_by_domain(self, domain_to_delete):
        if not self.token:
            return False, "未配置 Cloudflare Token"
        if not domain_to_delete:
            return False, "域名为空"

        if self.root_domain not in domain_to_delete:
            return False, f"安全拦截: {domain_to_delete} 不属于根域名 {self.root_domain}"

        def _task():
            zone_id, err = self.get_zone_id(domain_to_delete)
            if not zone_id:
                return False, f"找不到 Zone: {err}"

            search_url = f"{self.base_url}/zones/{zone_id}/dns_records?name={domain_to_delete}"
            try:
                r = requests.get(search_url, headers=self._headers(), timeout=10)
                data = r.json()
                if not data.get('success'):
                    return False, "查询记录失败"

                records = data.get('result', [])
                if not records:
                    return True, "记录不存在，无需删除"

                deleted_count = 0
                for rec in records:
                    rec_id = rec['id']
                    del_url = f"{self.base_url}/zones/{zone_id}/dns_records/{rec_id}"
                    requests.delete(del_url, headers=self._headers(), timeout=5)
                    deleted_count += 1

                return True, f"已清理 {deleted_count} 条 DNS 记录"

            except Exception as e:
                return False, str(e)

        return await run.io_bound(_task)
