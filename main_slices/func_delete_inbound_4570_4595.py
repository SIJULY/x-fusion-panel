async def delete_inbound(mgr, id, cb):
    try:
        success, msg = False, ""
        
        # 1. 判断是否是 SSH 管理器 (通过检查是否有 _exec_remote_script 方法)
        is_ssh_manager = hasattr(mgr, '_exec_remote_script')
        
        if is_ssh_manager:
            # SSH 模式 (我们在 SSHXUIManager 里定义的是 async 方法，直接 await)
            success, msg = await mgr.delete_inbound(id)
        else:
            # HTTP 模式 (XUIManager 里是同步方法，必须放入 io_bound 线程池防止卡顿)
            success, msg = await run.io_bound(mgr.delete_inbound, id)

        # 2. 处理结果
        if success:
            safe_notify(f"✅ {msg}", "positive")
            # 执行回调刷新 UI (例如刷新列表)
            if cb:
                res = cb()
                if asyncio.iscoroutine(res): await res
        else:
            safe_notify(f"❌ 删除失败: {msg}", "negative")
            
    except Exception as e:
        safe_notify(f"❌ 系统异常: {str(e)}", "negative")