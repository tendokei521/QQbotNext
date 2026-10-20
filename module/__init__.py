"""插件**运行时数据**包。

历史上这里还放着业务插件代码（`module/modules/<name>/`），P5 已搬到
`app/plugins/<name>/`。现在本目录只剩运行时数据，且仍作为一个包存在，
因为外部 zip 插件会装到 `module/plugins/<name>/`（`app/core/settings.py::zip_plugins_dir`）。

- 业务插件代码 → `app/plugins/`（随仓库）
- 插件框架     → `app/plugin/`
- 本目录       → `configs/`（每模块配置）、`data/`（持久化数据）、
                 `plugins/`（zip 安装）、`uninstalled_modules.json`
"""
