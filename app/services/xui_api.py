import requests

from app.core.logging import logger


class XUIManager:
    @staticmethod
    def _normalize_inbound_item(item):
        if not isinstance(item, dict):
            return item

        import json

        if item.get('stream_settings') and not item.get('streamSettings'):
            item['streamSettings'] = item.get('stream_settings')

        for key in ['settings', 'streamSettings', 'stream_settings', 'sniffing']:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                try:
                    item[key] = json.loads(value)
                except:
                    pass

        if item.get('stream_settings') and not item.get('streamSettings'):
            item['streamSettings'] = item.get('stream_settings')

        item['expiryTime'] = int(item.get('expiryTime') or item.get('expiry_time') or 0)
        if 'enable' in item:
            item['enable'] = bool(item.get('enable'))
        return item

    def __init__(self, url, username, password, api_prefix=None):
        self.original_url = str(url).strip().rstrip('/')
        self.url = self.original_url
        self.username = str(username).strip()
        self.password = str(password).strip()
        self.api_prefix = f"/{api_prefix.strip('/')}" if api_prefix else None
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0', 'Connection': 'close'})
        self.session.verify = False
        self.login_path = None

    def _request(self, method, path, **kwargs):
        target_url = f"{self.url}{path}"
        for attempt in range(2):
            try:
                if method == 'POST':
                    return self.session.post(target_url, timeout=30, allow_redirects=False, **kwargs)
                else:
                    return self.session.get(target_url, timeout=30, allow_redirects=False, **kwargs)
            except Exception:
                if attempt == 1:
                    return None

    def login(self):
        if self.login_path:
            if self._try_login_at(self.login_path):
                return True
            self.login_path = None
        paths = ['/login', '/xui/login', '/panel/login']
        if self.api_prefix:
            paths.insert(0, f"{self.api_prefix}/login")
        protocols = [self.original_url]
        if '://' not in self.original_url:
            protocols = [f"http://{self.original_url}", f"https://{self.original_url}"]
        elif self.original_url.startswith('http://'):
            protocols.append(self.original_url.replace('http://', 'https://'))
        elif self.original_url.startswith('https://'):
            protocols.append(self.original_url.replace('https://', 'http://'))
        for proto_url in protocols:
            self.url = proto_url
            for path in paths:
                if self._try_login_at(path):
                    self.login_path = path
                    return True
        return False

    def _try_login_at(self, path):
        try:
            r = self._request('POST', path, data={'username': self.username, 'password': self.password})
            if r and r.status_code == 200 and r.json().get('success') is True:
                return True
            return False
        except:
            return False

    def get_inbounds(self):
        if not self.login():
            return None
        candidates = []
        if self.login_path:
            candidates.append(self.login_path.replace('login', 'inbound/list'))
        defaults = ['/xui/inbound/list', '/panel/inbound/list', '/inbound/list']
        if self.api_prefix:
            defaults.insert(0, f"{self.api_prefix}/inbound/list")
        for d in defaults:
            if d not in candidates:
                candidates.append(d)
        for path in candidates:
            r = self._request('POST', path)
            if r and r.status_code == 200:
                try:
                    res = r.json()
                    if res.get('success'):
                        obj = res.get('obj')
                        if isinstance(obj, list):
                            return [self._normalize_inbound_item(x) for x in obj]
                        return obj
                except:
                    pass
        return None

    def get_server_status(self):
        """获取服务器系统状态 (CPU, 内存, 硬盘, Uptime)"""
        if not self.login():
            return None

        candidates = []
        if self.login_path:
            candidates.append(self.login_path.replace('login', 'server/status'))
        defaults = ['/xui/server/status', '/panel/server/status', '/server/status']
        if self.api_prefix:
            defaults.insert(0, f"{self.api_prefix}/server/status")

        for d in defaults:
            if d not in candidates:
                candidates.append(d)

        for path in candidates:
            try:
                r = self._request('POST', path)
                if r and r.status_code == 200:
                    res = r.json()
                    if res.get('success'):
                        return res.get('obj')
            except:
                pass
        return None

    def add_inbound(self, data):
        return self._action('/add', data)

    def update_inbound(self, iid, data):
        return self._action(f'/update/{iid}', data)

    def delete_inbound(self, iid):
        return self._action(f'/del/{iid}', {})

    def _action(self, suffix, data):
        if not self.login():
            logger.error("❌ [API] 未登录，无法执行操作")
            return False, "登录失败"

        if self.login_path == '/login':
            base = '/xui/inbound'
        else:
            base = self.login_path.replace('/login', '/inbound')
        path = f"{base}{suffix}"

        import json
        payload = {}
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                payload[k] = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, bool):
                payload[k] = 'true' if v else 'false'
            else:
                payload[k] = str(v)

        target_url = f"{self.url}{path}"
        logger.info(f"🔍 [API Debug] 发送目标: {target_url}")
        logger.info(f"📦 [API Debug] 原始数据: {data}")
        logger.info(f"🚀 [API Debug] 序列化后: {payload}")

        try:
            logger.info("⏳ [API Debug] 等待 X-UI 后端响应 (最长30秒)...")

            headers = {'Accept': 'application/json'}
            r = self.session.post(target_url, data=payload, headers=headers, timeout=30, allow_redirects=False)

            logger.info(f"✅ [API Debug] 响应状态码: {r.status_code}")
            logger.info(f"📄 [API Debug] 原始返回内容: {r.text[:1000]}")

            if r.status_code == 200:
                resp = r.json()
                if resp.get('success'):
                    return True, resp.get('msg')
                else:
                    return False, f"后端拒绝: {resp.get('msg')}"
            else:
                return False, f"HTTP错误: {r.status_code}"

        except Exception as e:
            import traceback
            logger.error(f"❌ [API Debug] 网络底层错误: {str(e)}")
            logger.error(traceback.format_exc())
            return False, f"请求异常: {str(e)}"
