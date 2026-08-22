import os
import posixpath
import stat

from app.services.ssh import get_ssh_client


TEXT_FILE_EXTENSIONS = {
    '.txt', '.log', '.json', '.yaml', '.yml', '.conf', '.ini', '.sh', '.py', '.js', '.ts', '.css', '.html', '.md',
    '.xml', '.toml', '.env', '.service', '.rules', '.sql', '.csv', '.properties', '.cfg', '.cnf', '.lst', '.repo',
}
TEXT_FILE_NAMES = {
    'docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml', 'nginx.conf', 'caddyfile',
    'hosts', 'fstab', 'crontab', '.bashrc', '.zshrc', '.profile', '.env',
}


def normalize_remote_path(path: str) -> str:
    path = (path or '').strip()
    if not path:
        return '/'
    normalized = posixpath.normpath(path)
    if not normalized.startswith('/'):
        normalized = '/' + normalized
    return normalized or '/'


def join_remote_path(base: str, name: str) -> str:
    base = normalize_remote_path(base)
    return normalize_remote_path(posixpath.join(base, name))


def get_parent_remote_path(path: str) -> str:
    path = normalize_remote_path(path)
    parent = posixpath.dirname(path)
    return parent if parent else '/'


def is_probably_text_file(path: str) -> bool:
    name = posixpath.basename(path or '').lower()
    if name in TEXT_FILE_NAMES:
        return True
    _, ext = posixpath.splitext(name)
    return ext in TEXT_FILE_EXTENSIONS


async def _open_sftp(server_conf):
    client, msg = await get_ssh_client(server_conf)
    if not client:
        raise RuntimeError(msg)
    return client, await client.start_sftp_client()


async def list_remote_dir(server_conf, path='/'):
    client = None
    sftp = None
    try:
        client, sftp = await _open_sftp(server_conf)
        path = normalize_remote_path(path)
        entries = []
        for attr in await sftp.readdir(path):
            if attr.filename in ('.', '..'):
                continue
            full_path = join_remote_path(path, attr.filename)
            is_dir = stat.S_ISDIR(attr.attrs.permissions) if attr.attrs.permissions else False
            entries.append({
                'name': attr.filename,
                'path': full_path,
                'is_dir': is_dir,
                'size': int(attr.attrs.size or 0),
                'mtime': int(attr.attrs.mtime or 0),
                'mode': stat.filemode(attr.attrs.permissions) if attr.attrs.permissions else '----------',
            })
        entries.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
        return entries
    finally:
        try:
            if sftp:
                sftp.exit()
        except:
            pass
        try:
            if client:
                client.close()
        except:
            pass


async def read_remote_file(server_conf, path: str, max_size=1024 * 1024):
    client = None
    sftp = None
    try:
        client, sftp = await _open_sftp(server_conf)
        path = normalize_remote_path(path)
        st = await sftp.stat(path)
        if st.permissions and stat.S_ISDIR(st.permissions):
            raise IsADirectoryError(path)
        if st.size and st.size > max_size:
            raise ValueError(f'文件过大，超过 {max_size // 1024} KB 限制')
        
        async with sftp.open(path, 'rb') as f:
            raw = await f.read()
            
        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError('文件不是 UTF-8 文本，暂不支持在线编辑')
        return {'path': path, 'size': int(st.size or 0), 'content': content}
    finally:
        try:
            if sftp:
                sftp.exit()
        except:
            pass
        try:
            if client:
                client.close()
        except:
            pass


async def write_remote_file(server_conf, path: str, content: str):
    client = None
    sftp = None
    try:
        client, sftp = await _open_sftp(server_conf)
        path = normalize_remote_path(path)
        async with sftp.open(path, 'wb') as f:
            await f.write((content or '').encode('utf-8'))
        return True
    finally:
        try:
            if sftp:
                sftp.exit()
        except:
            pass
        try:
            if client:
                client.close()
        except:
            pass


async def upload_remote_file(server_conf, local_path: str, remote_path: str):
    client = None
    sftp = None
    try:
        client, sftp = await _open_sftp(server_conf)
        remote_path = normalize_remote_path(remote_path)
        await sftp.put(local_path, remote_path)
        return True
    finally:
        try:
            if sftp:
                sftp.exit()
        except:
            pass
        try:
            if client:
                client.close()
        except:
            pass


async def download_remote_file(server_conf, remote_path: str):
    client = None
    sftp = None
    try:
        client, sftp = await _open_sftp(server_conf)
        remote_path = normalize_remote_path(remote_path)
        async with sftp.open(remote_path, 'rb') as f:
            data = await f.read()
        return data
    finally:
        try:
            if sftp:
                sftp.exit()
        except:
            pass
        try:
            if client:
                client.close()
        except:
            pass


async def make_remote_dir(server_conf, path: str):
    client = None
    sftp = None
    try:
        client, sftp = await _open_sftp(server_conf)
        await sftp.mkdir(normalize_remote_path(path))
        return True
    finally:
        try:
            if sftp:
                sftp.exit()
        except:
            pass
        try:
            if client:
                client.close()
        except:
            pass


async def create_empty_remote_file(server_conf, path: str):
    client = None
    sftp = None
    try:
        client, sftp = await _open_sftp(server_conf)
        async with sftp.open(normalize_remote_path(path), 'wb') as f:
            await f.write(b'')
        return True
    finally:
        try:
            if sftp:
                sftp.exit()
        except:
            pass
        try:
            if client:
                client.close()
        except:
            pass


async def rename_remote_path(server_conf, old_path: str, new_path: str):
    client = None
    sftp = None
    try:
        client, sftp = await _open_sftp(server_conf)
        await sftp.rename(normalize_remote_path(old_path), normalize_remote_path(new_path))
        return True
    finally:
        try:
            if sftp:
                sftp.exit()
        except:
            pass
        try:
            if client:
                client.close()
        except:
            pass


async def delete_remote_path(server_conf, path: str):
    client = None
    sftp = None

    async def _delete_recursive(target_path: str):
        st = await sftp.stat(target_path)
        if st.permissions and stat.S_ISDIR(st.permissions):
            for attr in await sftp.readdir(target_path):
                if attr.filename in ('.', '..'):
                    continue
                child = join_remote_path(target_path, attr.filename)
                await _delete_recursive(child)
            await sftp.rmdir(target_path)
        else:
            await sftp.remove(target_path)

    try:
        client, sftp = await _open_sftp(server_conf)
        await _delete_recursive(normalize_remote_path(path))
        return True
    finally:
        try:
            if sftp:
                sftp.exit()
        except:
            pass
        try:
            if client:
                client.close()
        except:
            pass