import base64
import json
import os
import secrets
import time
from typing import Any, Dict
from urllib.parse import urlencode

import httpx
from nicegui import run

from app.core.state import ADMIN_CONFIG, NODES_DATA, SERVERS_CACHE, SUBS_CACHE
from app.storage.repositories import load_global_key, save_admin_config

DEFAULT_BACKUP_REPO = os.getenv('GITHUB_BACKUP_REPO', 'x-fusion-panel-backups').strip()
DEFAULT_BACKUP_DIR = os.getenv('GITHUB_BACKUP_DIR', 'backups').strip()
DEFAULT_GITHUB_CLIENT_ID = os.getenv('GITHUB_CLIENT_ID', '').strip()
DEFAULT_GITHUB_CLIENT_SECRET = os.getenv('GITHUB_CLIENT_SECRET', '').strip()
LATEST_BACKUP_FILENAME = 'x_fusion_backup_latest.json'
GITHUB_OAUTH_CALLBACK_PATH = '/api/github/oauth/callback'
GITHUB_OAUTH_STATE_TTL = 600

_GITHUB_API = 'https://api.github.com'
_GITHUB_AUTHORIZE_URL = 'https://github.com/login/oauth/authorize'
_GITHUB_DEVICE_TOKEN_URL = 'https://github.com/login/oauth/access_token'
_GITHUB_SENSITIVE_KEYS = {
    'github_access_token',
    'github_client_id',
    'github_client_secret',
    'github_oauth_state',
    'github_oauth_state_created_at',
}


class GitHubBackupError(Exception):
    pass


def get_github_client_id() -> str:
    return (ADMIN_CONFIG.get('github_client_id') or DEFAULT_GITHUB_CLIENT_ID or '').strip()


def get_github_client_secret() -> str:
    return (ADMIN_CONFIG.get('github_client_secret') or DEFAULT_GITHUB_CLIENT_SECRET or '').strip()


def is_github_oauth_configured() -> bool:
    return bool(get_github_client_id() and get_github_client_secret())


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
        'github_oauth_last_success_at',
    ]:
        ADMIN_CONFIG.pop(key, None)


def clear_github_oauth_state() -> None:
    ADMIN_CONFIG.pop('github_oauth_state', None)
    ADMIN_CONFIG.pop('github_oauth_state_created_at', None)


async def build_full_backup_payload() -> Dict[str, Any]:
    admin_snapshot = json.loads(json.dumps(ADMIN_CONFIG, ensure_ascii=False))
    for key in _GITHUB_SENSITIVE_KEYS:
        admin_snapshot.pop(key, None)

    return {
        'version': '3.2',
        'timestamp': time.time(),
        'servers': json.loads(json.dumps(SERVERS_CACHE, ensure_ascii=False)),
        'subscriptions': json.loads(json.dumps(SUBS_CACHE, ensure_ascii=False)),
        'admin_config': admin_snapshot,
        'global_ssh_key': await load_global_key(),
        'cache': json.loads(json.dumps(NODES_DATA, ensure_ascii=False)),
    }


def resolve_app_origin(request_origin: str = '') -> str:
    configured = (ADMIN_CONFIG.get('manager_base_url') or '').strip().rstrip('/')
    if configured:
        return configured
    return (request_origin or '').strip().rstrip('/')


def build_github_callback_url(app_origin: str) -> str:
    origin = resolve_app_origin(app_origin)
    if not origin:
        raise GitHubBackupError('未能识别当前面板访问地址，请先在系统设置中保存主控端地址')
    return f'{origin}{GITHUB_OAUTH_CALLBACK_PATH}'


async def prepare_github_oauth_start_url(app_origin: str) -> str:
    client_id = get_github_client_id()
    client_secret = get_github_client_secret()
    if not client_id or not client_secret:
        raise GitHubBackupError('请先在网页中保存 GitHub Client ID 和 Client Secret')

    callback_url = build_github_callback_url(app_origin)
    state = secrets.token_urlsafe(32)
    ADMIN_CONFIG['github_oauth_state'] = state
    ADMIN_CONFIG['github_oauth_state_created_at'] = time.time()
    await save_admin_config()

    query = urlencode({
        'client_id': client_id,
        'redirect_uri': callback_url,
        'scope': 'repo read:user',
        'state': state,
    })
    return f'{_GITHUB_AUTHORIZE_URL}?{query}'


async def fetch_github_user(access_token: str | None = None) -> Dict[str, Any]:
    token = (access_token or get_github_access_token()).strip()
    if not token:
        raise GitHubBackupError('未连接 GitHub 账号')

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f'{_GITHUB_API}/user',
            headers={
                'Accept': 'application/vnd.github+json',
                'Authorization': f'Bearer {token}',
                'X-GitHub-Api-Version': '2022-11-28',
            },
            timeout=20.0,
        )
        data = resp.json()
        if resp.status_code >= 400 or data.get('message') == 'Bad credentials':
            raise GitHubBackupError(data.get('message') or 'GitHub 用户信息获取失败')
        return data


async def save_github_auth(access_token: str) -> Dict[str, Any]:
    profile = await fetch_github_user(access_token)
    ADMIN_CONFIG['github_access_token'] = access_token
    ADMIN_CONFIG['github_user_login'] = profile.get('login', '')
    ADMIN_CONFIG['github_user_name'] = profile.get('name') or profile.get('login', '')
    ADMIN_CONFIG['github_oauth_last_success_at'] = time.time()
    if not ADMIN_CONFIG.get('github_backup_repo'):
        ADMIN_CONFIG['github_backup_repo'] = DEFAULT_BACKUP_REPO or 'x-fusion-panel-backups'
    if not ADMIN_CONFIG.get('github_backup_dir'):
        ADMIN_CONFIG['github_backup_dir'] = DEFAULT_BACKUP_DIR or 'backups'
    return profile


async def complete_github_oauth(code: str, state: str, app_origin: str) -> Dict[str, Any]:
    if not code:
        raise GitHubBackupError('GitHub 未返回授权 code')

    expected_state = (ADMIN_CONFIG.get('github_oauth_state') or '').strip()
    created_at = float(ADMIN_CONFIG.get('github_oauth_state_created_at') or 0)
    if not expected_state or state != expected_state:
        raise GitHubBackupError('GitHub 授权状态校验失败，请重新发起授权')
    if created_at and time.time() - created_at > GITHUB_OAUTH_STATE_TTL:
        clear_github_oauth_state()
        await save_admin_config()
        raise GitHubBackupError('GitHub 授权已超时，请重新发起授权')

    client_id = get_github_client_id()
    client_secret = get_github_client_secret()
    callback_url = build_github_callback_url(app_origin)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _GITHUB_DEVICE_TOKEN_URL,
            headers={'Accept': 'application/json'},
            data={
                'client_id': client_id,
                'client_secret': client_secret,
                'code': code,
                'redirect_uri': callback_url,
                'state': state,
            },
            timeout=20.0,
        )
        data = resp.json()
        if resp.status_code >= 400 or data.get('error'):
            raise GitHubBackupError(data.get('error_description') or data.get('error') or 'GitHub OAuth 换取 token 失败')

    profile = await save_github_auth(data.get('access_token', ''))
    clear_github_oauth_state()
    await save_admin_config()
    return profile


def _api_headers(token: str) -> Dict[str, str]:
    return {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {token}',
        'X-GitHub-Api-Version': '2022-11-28',
    }


async def _ensure_repo_exists_async(client: httpx.AsyncClient, token: str, owner: str, repo: str) -> None:
    repo_url = f'{_GITHUB_API}/repos/{owner}/{repo}'
    repo_resp = await client.get(repo_url, headers=_api_headers(token), timeout=20.0)
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

    create_resp = await client.post(
        f'{_GITHUB_API}/user/repos',
        headers=_api_headers(token),
        json={
            'name': repo,
            'description': 'Private backup repository for X-Fusion Panel',
            'private': True,
            'auto_init': True,
        },
        timeout=25.0,
    )
    create_data = create_resp.json()
    if create_resp.status_code >= 400:
        raise GitHubBackupError(create_data.get('message') or '创建私有备份仓库失败')


async def _get_content_sha_async(client: httpx.AsyncClient, token: str, owner: str, repo: str, path: str) -> str | None:
    resp = await client.get(f'{_GITHUB_API}/repos/{owner}/{repo}/contents/{path}', headers=_api_headers(token), timeout=20.0)
    if resp.status_code == 200:
        return resp.json().get('sha')
    if resp.status_code == 404:
        return None
    try:
        detail = resp.json().get('message')
    except Exception:
        detail = resp.text
    raise GitHubBackupError(detail or '获取 GitHub 文件信息失败')


async def _put_content_async(client: httpx.AsyncClient, token: str, owner: str, repo: str, path: str, content_bytes: bytes, message: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        'message': message,
        'content': base64.b64encode(content_bytes).decode('utf-8'),
    }
    sha = await _get_content_sha_async(client, token, owner, repo, path)
    if sha:
        payload['sha'] = sha

    resp = await client.put(
        f'{_GITHUB_API}/repos/{owner}/{repo}/contents/{path}',
        headers=_api_headers(token),
        json=payload,
        timeout=30.0,
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
    payload = backup_payload or await build_full_backup_payload()
    content_bytes = json.dumps(payload, indent=2, ensure_ascii=False).encode('utf-8')

    async with httpx.AsyncClient() as client:
        await _ensure_repo_exists_async(client, token, owner, repo)
        await _put_content_async(client, token, owner, repo, latest_path, content_bytes, 'chore: update latest X-Fusion backup')
        history_result = await _put_content_async(client, token, owner, repo, history_path, content_bytes, f'chore: create X-Fusion backup {timestamp_str}')
        return {
            'owner': owner,
            'repo': repo,
            'latest_path': latest_path,
            'history_path': history_path,
            'html_url': (((history_result.get('content') or {}).get('html_url')) or ''),
        }


async def download_latest_backup_from_github() -> Dict[str, Any]:
    token = get_github_access_token()
    if not token:
        raise GitHubBackupError('请先连接 GitHub 账号')

    profile = await fetch_github_user(token)
    owner = profile.get('login')
    repo = get_github_backup_repo()
    path = get_github_backup_path()

    async with httpx.AsyncClient() as client:
        resp = await client.get(f'{_GITHUB_API}/repos/{owner}/{repo}/contents/{path}', headers=_api_headers(token), timeout=25.0)
        data = resp.json()
        if resp.status_code >= 400:
            raise GitHubBackupError(data.get('message') or '下载 GitHub 备份失败')
        raw_content = (data.get('content') or '').replace('\n', '')
        if not raw_content:
            raise GitHubBackupError('GitHub 备份文件内容为空')
        decoded = base64.b64decode(raw_content).decode('utf-8')
        return json.loads(decoded)