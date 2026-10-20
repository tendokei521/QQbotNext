"""基础设施层的文件系统出口。

按 `docs/ARCHITECTURE.md §3.4`，**全库只有 `app/infrastructure/` 允许直接做文件 IO**。
本子包集中所有「路径计算 + 落盘/读取」的工具函数，业务层只调用这里的函数，
不再自己 `open(..., "w")` / `os.makedirs`。
"""
