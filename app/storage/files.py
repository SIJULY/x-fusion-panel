import asyncio
import json
import os
import uuid
import aiofiles
import aiofiles.os

from app.core.logging import logger


FILE_LOCK = asyncio.Lock()


async def safe_save(filename, data):
    async with FILE_LOCK:
        temp_file = f"{filename}.{uuid.uuid4()}.tmp"
        try:
            json_str = json.dumps(data, indent=4, ensure_ascii=False)
            async with aiofiles.open(temp_file, 'w', encoding='utf-8') as f:
                await f.write(json_str)
            await aiofiles.os.rename(temp_file, filename)
        except Exception as e:
            if await aiofiles.os.path.exists(temp_file):
                await aiofiles.os.remove(temp_file)
            logger.error(f"❌ 保存 {filename} 失败: {e}")
