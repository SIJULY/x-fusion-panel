import os
import posixpath
import stat

from app.services.ssh import get_ssh_client_sync


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


def _open_sftp(server_conf):
    client, msg = get_ssh_client_sync(server_conf)
    if not client:
        raise RuntimeError(msg)
    return client, client.open_sftp()


def list_remote_dir(server_conf, path='/'):
    client = None
    sftp = None
    try:
        client, sftp = _open_sftp(server_conf)
        path = normalize_remote_path(path)
        entries = []
        for attr in sftp.listdir_attr(path):
            full_path = join_remote_path(path, attr.filename)
            is_dir = stat.S_ISDIR(attr.st_mode)
            entries.append({
                'name': attr.filename,
                'path': full_path,
                'is_dir': is_dir,
                'size': int(getattr(attr, 'st_size', 0) or 0),
                'mtime': int(getattr(attr, 'st_mtime', 0) or 0),
                'mode': stat.filemode(attr.st_mode),
            })
        entries.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
        return entries
    finally:
        try:
            if sftp:
                sftp.close()
        except:
            pass
        try:
            if client:
                client.close()
        except:
            pass


def read_remote_file(server_conf, path: str, max_size=1024 * 1024):
    client = None
    sftp = None
    try:
        client, sftp = _open_sftp(server_conf)
        path = normalize_remote_path(path)
        st = sftp.stat(path)
        if stat.S_ISDIR(st.st_mode):
            raise IsADirectoryError(path)
        if st.st_size > max_size:
            raise ValueError(f'文件过大，超过 {max_size // 1024} KB 限制')
        with sftp.open(path, 'rb') as f:
            raw = f.read()
        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError('文件不是 UTF-8 文本，暂不支持在线编辑')
        return {'path': path, 'size': int(st.st_size), 'content': content}
    finally:
        try:
            if sftp:
                sftp.close()
        except:
            pass
        try:
            if client:
                client.close()
        except:
            pass


def write_remote_file(server_conf, path: str, content: str):
    client = None
    sftp = None
    try:
        client, sftp = _open_sftp(server_conf)
        path = normalize_remote_path(path)
        with sftp.open(path, 'wb') as f:
            f.write((content or '').encode('utf-8'))
        return True
    finally:
        try:
            if sftp:
                sftp.close()
        except:
            pass
        try:
            if client:
                client.close()
        except:
            pass


def upload_remote_file(server_conf, local_path: str, remote_path: str):
    client = None
    sftp = None
    try:
        client, sftp = _open_sftp(server_conf)
        remote_path = normalize_remote_path(remote_path)
        sftp.put(local_path, remote_path)
        return True
    finally:
        try:
            if sftp:
                sftp.close()
        except:
            pass
        try:
            if client:
                client.close()
        except:
            pass


def download_remote_file(server_conf, remote_path: str):
    client = None
    sftp = None
    try:
        client, sftp = _open_sftp(server_conf)
        remote_path = normalize_remote_path(remote_path)
        with sftp.open(remote_path, 'rb') as f:
            data = f.read()
        return data
    finally:
        try:
            if sftp:
                sftp.close()
        except:
            pass
        try:
            if client:
                client.close()
        except:
            pass


def make_remote_dir(server_conf, path: str):
    client = None
    sftp = None
    try:
        client, sftp = _open_sftp(server_conf)
        sftp.mkdir(normalize_remote_path(path))
        return True
    finally:
        try:
            if sftp:
                sftp.close()
        except:
            pass
        try:
            if client:
                client.close()
        except:
            pass


def create_empty_remote_file(server_conf, path: str):
    client = None
    sftp = None
    try:
        client, sftp = _open_sftp(server_conf)
        with sftp.open(normalize_remote_path(path), 'wb') as f:
            f.write(b'')
        return True
    finally:
        try:
            if sftp:
                sftp.close()
        except:
            pass
        try:
            if client:
                client.close()
        except:
            pass


def rename_remote_path(server_conf, old_path: str, new_path: str):
    client = None
    sftp = None
    try:
        client, sftp = _open_sftp(server_conf)
        sftp.rename(normalize_remote_path(old_path), normalize_remote_path(new_path))
        return True
    finally:
        try:
            if sftp:
                sftp.close()
        except:
            pass
        try:
            if client:
                client.close()
        except:
            pass


def delete_remote_path(server_conf, path: str):
    client = None
    sftp = None

    def _delete_recursive(target_path: str):
        st = sftp.stat(target_path)
        if stat.S_ISDIR(st.st_mode):
            for attr in sftp.listdir_attr(target_path):
                child = join_remote_path(target_path, attr.filename)
                _delete_recursive(child)
            sftp.rmdir(target_path)
        else:
            sftp.remove(target_path)

    try:
        client, sftp = _open_sftp(server_conf)
        _delete_recursive(normalize_remote_path(path))
        return True
    finally:
        try:
            if sftp:
                sftp.close()
        except:
            pass
        try:
            if client:
                client.close()
        except:
            pass
