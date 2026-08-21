from app.services.xui_api import XUIManager
from app.services.xui_ssh import SSHXUIManager


managers = {}


class HybridManager:
    """
    混合管理器：优先尝试 SSH（若可用且配置了），如果 SSH 失败或权限不足，自动降级回退到 API 模式。
    """
    def __init__(self, server_conf):
        self.server_conf = server_conf
        self.url = server_conf.get('url')
        self._ssh_mgr = None
        self._api_mgr = None

    def _get_ssh_mgr(self):
        if not self._ssh_mgr:
            if self.server_conf.get('probe_installed') and self.server_conf.get('ssh_host'):
                self._ssh_mgr = SSHXUIManager(self.server_conf)
        return self._ssh_mgr

    def _get_api_mgr(self):
        if not self._api_mgr:
            if self.url and self.server_conf.get('user') and self.server_conf.get('pass'):
                self._api_mgr = XUIManager(
                    self.url, 
                    self.server_conf['user'], 
                    self.server_conf['pass'], 
                    self.server_conf.get('prefix')
                )
        return self._api_mgr

    async def get_inbounds(self):
        ssh_mgr = self._get_ssh_mgr()
        api_mgr = self._get_api_mgr()
        last_err = None
        
        if ssh_mgr:
            try:
                return await ssh_mgr.get_inbounds()
            except Exception as e:
                last_err = e
        
        if api_mgr:
            return await api_mgr.get_inbounds()
            
        raise last_err or Exception("无法获取节点：请配置面板 API 账号密码，或确保 SSH 连接可用")

    async def add_inbound(self, inbound_data):
        ssh_mgr = self._get_ssh_mgr()
        api_mgr = self._get_api_mgr()
        last_err = None
        
        if ssh_mgr:
            try:
                return await ssh_mgr.add_inbound(inbound_data)
            except Exception as e:
                last_err = e
        
        if api_mgr:
            return await api_mgr.add_inbound(inbound_data)
            
        raise last_err or Exception("无法添加节点：请配置面板 API 账号密码，或确保 SSH 连接可用")

    async def update_inbound(self, inbound_id, inbound_data):
        ssh_mgr = self._get_ssh_mgr()
        api_mgr = self._get_api_mgr()
        last_err = None
        
        if ssh_mgr:
            try:
                return await ssh_mgr.update_inbound(inbound_id, inbound_data)
            except Exception as e:
                last_err = e
        
        if api_mgr:
            return await api_mgr.update_inbound(inbound_id, inbound_data)
            
        raise last_err or Exception("无法更新节点：请配置面板 API 账号密码，或确保 SSH 连接可用")

    async def delete_inbound(self, inbound_id):
        ssh_mgr = self._get_ssh_mgr()
        api_mgr = self._get_api_mgr()
        last_err = None
        
        if ssh_mgr:
            try:
                return await ssh_mgr.delete_inbound(inbound_id)
            except Exception as e:
                last_err = e
        
        if api_mgr:
            return await api_mgr.delete_inbound(inbound_id)
            
        raise last_err or Exception("无法删除节点：请配置面板 API 账号密码，或确保 SSH 连接可用")


def get_manager(server_conf):
    url = server_conf.get('url') or server_conf.get('ssh_host')
    mgr_key = f"hybrid_{url}"
    
    if mgr_key in managers:
        mgr = managers[mgr_key]
        mgr.server_conf = server_conf
        mgr.url = url
        
        # Re-initialize inner managers to pick up new config
        if mgr._ssh_mgr:
            mgr._ssh_mgr.server_conf = server_conf
        if mgr._api_mgr:
            mgr._api_mgr.url = url
            mgr._api_mgr.username = server_conf.get('user')
            mgr._api_mgr.password = server_conf.get('pass')
            mgr._api_mgr.prefix = server_conf.get('prefix')
    else:
        managers[mgr_key] = HybridManager(server_conf)
        
    return managers[mgr_key]
