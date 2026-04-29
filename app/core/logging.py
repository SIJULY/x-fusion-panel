import logging
import sys
from concurrent.futures import ThreadPoolExecutor

import urllib3
from apscheduler.schedulers.asyncio import AsyncIOScheduler


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger('XUI_Manager')
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('nicegui').setLevel(logging.INFO)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BG_EXECUTOR = ThreadPoolExecutor(max_workers=20)
scheduler = AsyncIOScheduler()
