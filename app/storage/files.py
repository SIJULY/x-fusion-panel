import asyncio
import json
import os
import shutil
import uuid

from nicegui import run

from app.core.logging import logger


FILE_LOCK = asyncio.Lock()


def _save_file_sync_internal(filename, data):
    # 使用绝对路径生成临时文件
    temp_file = f"{filename}.{uuid.uuid4()}.tmp"
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        shutil.move(temp_file, filename)
    except Exception as e:
        if os.path.exists(temp_file):
            os.remove(temp_file)
        raise e


async def safe_save(filename, data):
    async with FILE_LOCK:
        try:
            await run.io_bound(_save_file_sync_internal, filename, data)
        except Exception as e:
            logger.error(f"❌ 保存 {filename} 失败: {e}")
