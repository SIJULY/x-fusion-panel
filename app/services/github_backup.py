import asyncio
import base64
import json
import os
import time
from typing import Any, Dict

import requests
from nicegui import run

from app.core.state import ADMIN_CONFIG, NODES_DATA, SERVERS_CACHE, SUBS_CACHE
from app.storage.repositories import load_global_key

GITHUB_CLIENT_ID = os.getenv('GITHUB_CLIENT_ID', '').strip()
DEFAULT_BACKUP_REPO = os.getenv('GITHUB_BACKUP_REPO', 'x-fusion-panel-backups').strip()
DEFAULT_BACKUP_DIR = os.getenv('GITHUB_BACKUP_DIR', 'backups').strip()
LATEST_BACKUP_FILENAME = 'x_fusion_backup_latest.json'

_GITHUB_API = 'https://api.github.com'
_GITHUB_DEVICE_CODE_URL = 'https://github.com/login/device/code'
_GITHUB_DEVICE_TOKEN_URL = 'https://github.com/login/oauth/access_token'
_GITHUB_SENSITIVE_KEYS = {
    'github_access_token',
    'github_device_code',
    'github_user_code',
    'github_verification_uri',
}


class GitHubBackupError(Exception):
    pass


def is_github_oauth_configured() -> bool:
    return bool(GITHUB_CLIENT_ID)


def get_github_backup_repo() -> str:
    return (ADMIN_CONFIG.get('github_backup_repo') or DEFAULT_BACKUP_REPO or 'x-fusion-panel-backups').strip()


def get_github_backup_dir() -> str:
    return (ADMIN_CONFIG.get('github_backup_dir') or DEFAULT_BACKUP_DIR or 'backups').strip().strip('/')


def get_github_backup_path() -> str:
    backup_dir = get_github_backup_dir()
    return f'{backup_dir}/{LATEST_BACKUP_FILENAME}' if backup_dir else LATEST_BACKUP_FILENAME


def get_github_access_token() -> str:
    return (ADMIN_CONFIG.get('github_access_token') or '').strip()


def is_github_connected() -> bool:
    return bool(get_github_access_token())


def clear_github_auth() -> None:
    for key in [
        'github_access_token',
        'github_user_login',
        'github_user_name',
        'github_device_code',
        'github_user_code',
        'github_verification_uri',
    ]:
        ADMIN_CONFIG.pop(key, None)


def build_full_backup_payload() -> Dict[str, Any]:
    admin_snapshot = json.loads(json.dumps(ADMIN_CONFIG, ensure_ascii=False))
    for key in _GITHUB_SENSITIVE_KEYS:
        admin_snapshot.pop(key, None)

    return {
        'version': '3.1',
        'timestamp': time.time(),
        'servers': json.loads(json.dumps(SERVERS_CACHE, ensure_ascii=False)),
        'subscriptions': json.loads(json.dumps(SUBS_CACHE, ensure_ascii=False)),
        'admin_config': admin_snapshot,
        'global_ssh_key': load_global_key(),
        'cache': json.loads(json.dumps(NODES_DATA, ensure_ascii=False)),
    }


async def start_device_flow() -> Dict[str, Any]:
    if not GITHUB_CLIENT_ID:
        raise GitHubBackupError('未配置 GITHUB_CLIENT_ID，无法启用 GitHub 授权')

    def _start() -> Dict[str, Any]:
        resp = requests.post(
            _GITHUB_DEVICE_CODE_URL,
            headers={'Accept': 'application/json'},
            data={'client_id': GITHUB_CLIENT_ID, 'scope': 'repo read:user'},
            timeout=15,
        )
        data = resp.json()
        if resp.status_code >= 400 or data.get('error'):
            raise GitHubBackupError(data.get('error_description') or data.get('error') or 'GitHub 设备授权启动失败')
        return data

    return await run.io_bound(_start)


async def poll_device_flow(device_code: str, interval: int = 5, expires_in: int = 900) -> Dict[str, Any]:
    if not device_code:
        raise GitHubBackupError('device_code 缺失')

    started_at = time.time()
    wait_seconds = max(int(interval or 5), 1)

    while time.time() - started_at < max(int(expires_in or 900), 60):
        def _poll() -> Dict[str, Any]:
            resp = requests.post(
                _GITHUB_DEVICE_TOKEN_URL,
                headers={'Accept': 'application/json'},
                data={
                    'client_id': GITHUB_CLIENT_ID,
                    'device_code': device_code,
                    'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
                },
                timeout=15,
            )
            return resp.json()

        data = await run.io_bound(_poll)
        if data.get('access_token'):
            return data

        error_code = data.get('error')
        if error_code == 'authorization_pending':
            await asyncio.sleep(wait_seconds)
            continue
        if error_code == 'slow_down':
            wait_seconds += 5
            await asyncio.sleep(wait_seconds)
            continue
        if error_code in {'expired_token', 'access_denied', 'incorrect_device_code', 'unsupported_grant_type'}:
            raise GitHubBackupError(data.get('error_description') or error_code)

        raise GitHubBackupError(data.get('error_description') or 'GitHub 授权轮询失败')

    raise GitHubBackupError('GitHub 授权已超时，请重新发起授权')


async def fetch_github_user(access_token: str | None = None) -> Dict[str, Any]:
    token = (access_token or get_github_access_token()).strip()
    if not token:
        raise GitHubBackupError('未连接 GitHub 账号')

    def _fetch() -> Dict[str, Any]:
        resp = requests.get(
            f'{_GITHUB_API}/user',
            headers={
                'Accept': 'application/vnd.github+json',
                'Authorization': f'Bearer {token}',
                'X-GitHub-Api-Version': '2022-11-28',
            },
            timeout=20,
        )
        data = resp.json()
        if resp.status_code >= 400 or data.get('message') == 'Bad credentials':
            raise GitHubBackupError(data.get('message') or 'GitHub 用户信息获取失败')
        return data

    return await run.io_bound(_fetch)


async def save_github_auth(access_token: str) -> Dict[str, Any]:
    profile = await fetch_github_user(access_token)
    ADMIN_CONFIG['github_access_token'] = access_token
    ADMIN_CONFIG['github_user_login'] = profile.get('login', '')
    ADMIN_CONFIG['github_user_name'] = profile.get('name') or profile.get('login', '')
    if not ADMIN_CONFIG.get('github_backup_repo'):
        ADMIN_CONFIG['github_backup_repo'] = DEFAULT_BACKUP_REPO or 'x-fusion-panel-backups'
    if not ADMIN_CONFIG.get('github_backup_dir'):
        ADMIN_CONFIG['github_backup_dir'] = DEFAULT_BACKUP_DIR or 'backups'
    return profile


def _api_headers(token: str) -> Dict[str, str]:
    return {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {token}',
        'X-GitHub-Api-Version': '2022-11-28',
    }


def _ensure_repo_exists_sync(token: str, owner: str, repo: str) -> None:
    repo_url = f'{_GITHUB_API}/repos/{owner}/{repo}'
    repo_resp = requests.get(repo_url, headers=_api_headers(token), timeout=20)
    if repo_resp.status_code == 200:
        repo_data = repo_resp.json()
        if not repo_data.get('private', False):
            raise GitHubBackupError(f'仓库 {owner}/{repo} 不是私有仓库，请改用私有仓库')
        return

    if repo_resp.status_code not in {403, 404}:
        try:
            detail = repo_resp.json().get('message')
        except Exception:
            detail = repo_resp.text
        raise GitHubBackupError(detail or '检测 GitHub 备份仓库失败')

    create_resp = requests.post(
        f'{_GITHUB_API}/user/repos',
        headers=_api_headers(token),
        json={
            'name': repo,
            'description': 'Private backup repository for X-Fusion Panel',
            'private': True,
            'auto_init': True,
        },
        timeout=25,
    )
    create_data = create_resp.json()
    if create_resp.status_code >= 400:
        raise GitHubBackupError(create_data.get('message') or '创建私有备份仓库失败')


def _get_content_sha_sync(token: str, owner: str, repo: str, path: str) -> str | None:
    resp = requests.get(f'{_GITHUB_API}/repos/{owner}/{repo}/contents/{path}', headers=_api_headers(token), timeout=20)
    if resp.status_code == 200:
        return resp.json().get('sha')
    if resp.status_code == 404:
        return None
    try:
        detail = resp.json().get('message')
    except Exception:
        detail = resp.text
    raise GitHubBackupError(detail or '获取 GitHub 文件信息失败')


def _put_content_sync(token: str, owner: str, repo: str, path: str, content_bytes: bytes, message: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        'message': message,
        'content': base64.b64encode(content_bytes).decode('utf-8'),
    }
    sha = _get_content_sha_sync(token, owner, repo, path)
    if sha:
        payload['sha'] = sha

    resp = requests.put(
        f'{_GITHUB_API}/repos/{owner}/{repo}/contents/{path}',
        headers=_api_headers(token),
        json=payload,
        timeout=30,
    )
    data = resp.json()
    if resp.status_code >= 400:
        raise GitHubBackupError(data.get('message') or f'上传备份文件失败: {path}')
    return data


async def upload_backup_to_github(backup_payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    token = get_github_access_token()
    if not token:
        raise GitHubBackupError('请先连接 GitHub 账号')

    profile = await fetch_github_user(token)
    owner = profile.get('login')
    repo = get_github_backup_repo()
    backup_dir = get_github_backup_dir()
    latest_path = get_github_backup_path()
    timestamp_str = time.strftime('%Y%m%d_%H%M%S', time.localtime())
    history_path = f'{backup_dir}/x_fusion_backup_{timestamp_str}.json' if backup_dir else f'x_fusion_backup_{timestamp_str}.json'
    payload = backup_payload or build_full_backup_payload()
    content_bytes = json.dumps(payload, indent=2, ensure_ascii=False).encode('utf-8')

    def _upload() -> Dict[str, Any]:
        _ensure_repo_exists_sync(token, owner, repo)
        _put_content_sync(token, owner, repo, latest_path, content_bytes, 'chore: update latest X-Fusion backup')
        history_result = _put_content_sync(token, owner, repo, history_path, content_bytes, f'chore: create X-Fusion backup {timestamp_str}')
        return {
            'owner': owner,
            'repo': repo,
            'latest_path': latest_path,
            'history_path': history_path,
            'html_url': (((history_result.get('content') or {}).get('html_url')) or ''),
        }

    return await run.io_bound(_upload)


async def download_latest_backup_from_github() -> Dict[str, Any]:
    token = get_github_access_token()
    if not token:
        raise GitHubBackupError('请先连接 GitHub 账号')

    profile = await fetch_github_user(token)
    owner = profile.get('login')
    repo = get_github_backup_repo()
    path = get_github_backup_path()

    def _download() -> Dict[str, Any]:
        resp = requests.get(f'{_GITHUB_API}/repos/{owner}/{repo}/contents/{path}', headers=_api_headers(token), timeout=25)
        data = resp.json()
        if resp.status_code >= 400:
            raise GitHubBackupError(data.get('message') or '下载 GitHub 备份失败')
        raw_content = (data.get('content') or '').replace('\n', '')
        if not raw_content:
            raise GitHubBackupError('GitHub 备份文件内容为空')
        decoded = base64.b64decode(raw_content).decode('utf-8')
        return json.loads(decoded)

    return await run.io_bound(_download)
