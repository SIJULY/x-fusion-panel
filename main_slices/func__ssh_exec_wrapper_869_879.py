def _ssh_exec_wrapper(server_conf, cmd):
    client, msg = get_ssh_client_sync(server_conf)
    if not client: return False, msg
    try:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        client.close()
        return True, out + "\n" + err
    except Exception as e:
        return False, str(e)