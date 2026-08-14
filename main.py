"""QQBot Next 入口。"""

import asyncio

from app.bootstrap import run


if __name__ == "__main__":
    asyncio.run(run())
