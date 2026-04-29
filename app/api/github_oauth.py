import json

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services.github_backup import GitHubBackupError, complete_github_oauth, prepare_github_oauth_start_url, resolve_app_origin


def _request_origin(request: Request) -> str:
    proto = request.headers.get('x-forwarded-proto') or request.url.scheme
    host = request.headers.get('x-forwarded-host') or request.headers.get('host') or request.url.netloc
    return f'{proto}://{host}'.rstrip('/')


async def github_oauth_start(request: Request):
    try:
        start_url = await prepare_github_oauth_start_url(_request_origin(request))
        return RedirectResponse(start_url, status_code=302)
    except GitHubBackupError as e:
        return HTMLResponse(f'<h3>GitHub 授权启动失败</h3><p>{e}</p>', status_code=400)


async def github_oauth_callback(request: Request, code: str = '', state: str = ''):
    app_origin = resolve_app_origin(_request_origin(request))
    try:
        profile = await complete_github_oauth(code, state, _request_origin(request))
        payload = json.dumps({
            'type': 'xfusion_github_oauth',
            'status': 'success',
            'login': profile.get('login', ''),
        }, ensure_ascii=False)
        target_origin = json.dumps(app_origin or '*', ensure_ascii=False)
        html = f"""
        <!doctype html>
        <html lang="zh-CN">
        <head>
          <meta charset="utf-8" />
          <title>GitHub 授权成功</title>
          <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #08101d; color: #e2e8f0; display:flex; align-items:center; justify-content:center; min-height:100vh; margin:0; }}
            .card {{ width:min(520px, 92vw); padding:32px; border:1px solid rgba(34,211,238,0.35); background:#0a1120; border-radius:14px; box-shadow:0 18px 48px rgba(0,0,0,.45); }}
            h2 {{ margin:0 0 12px; color:#22d3ee; }}
            p {{ margin:8px 0; line-height:1.7; color:#cbd5e1; }}
          </style>
        </head>
        <body>
          <div class="card">
            <h2>GitHub 授权成功</h2>
            <p>已连接账号：@{profile.get('login', 'unknown')}</p>
            <p>此窗口将自动关闭；如果没有自动关闭，可手动关闭后返回面板继续云备份。</p>
          </div>
          <script>
            try {{
              if (window.opener && !window.opener.closed) {{
                window.opener.postMessage({payload}, {target_origin});
              }}
            }} catch (e) {{}}
            setTimeout(() => window.close(), 1200);
          </script>
        </body>
        </html>
        """
        return HTMLResponse(html)
    except GitHubBackupError as e:
        payload = json.dumps({'type': 'xfusion_github_oauth', 'status': 'error', 'message': str(e)}, ensure_ascii=False)
        target_origin = json.dumps(app_origin or '*', ensure_ascii=False)
        html = f"""
        <!doctype html>
        <html lang="zh-CN">
        <head><meta charset="utf-8" /><title>GitHub 授权失败</title></head>
        <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#111827;color:#f8fafc;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;">
          <div style="width:min(520px,92vw);padding:32px;border:1px solid rgba(239,68,68,0.35);background:#0f172a;border-radius:14px;">
            <h2 style="margin:0 0 12px;color:#f87171;">GitHub 授权失败</h2>
            <p style="line-height:1.7;">{e}</p>
            <p style="line-height:1.7;">请关闭此窗口后返回面板重新发起授权。</p>
          </div>
          <script>
            try {{
              if (window.opener && !window.opener.closed) {{
                window.opener.postMessage({payload}, {target_origin});
              }}
            }} catch (err) {{}}
          </script>
        </body>
        </html>
        """
        return HTMLResponse(html, status_code=400)
