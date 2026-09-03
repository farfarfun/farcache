# Changelog

本文件记录 `farcache` 的版本变更，按版本倒序排列，分为 新增 / 修复 / 变更 / 废弃 四类。

## [2.0.3] - 2026-08

### 修复

- 移除已误提交到版本管理的构建日志文件，`.gitignore` 补充 `logs/`、`.run/`、`*.db`、`*.rar`、`.idea/`、`.vscode/` 等规则。

## [2.0.0] - 2026-08

### 变更（破坏性）

1. **缓存键算法变更。** 旧缓存不会被读取，首次运行相当于全部重算。旧的 `.cache` / `.disk_cache` 目录可以直接删除。
2. **Python 最低版本提升到 3.10。**
3. `cache_key` 不再是必填参数，省略时以全部参数为键。
4. `vttl_cache` 的 `ttl` 之前从未生效（被传给了构造函数，只对初始化数据有效），现已按每条目过期正确实现。
5. `PickleCache` / `DiskCache` 的内部结构重写，`_cache`、`_get_cache_file`、`_load_cache`、`_save_cache` 等私有成员已移除；公开的 `cache_clear()` 等方法取代了它们。

详见 README 「从 1.x 升级」一节。
