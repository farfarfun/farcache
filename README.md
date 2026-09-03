# farcache

一个简洁的 Python 函数缓存装饰器库，提供多种缓存策略，涵盖内存缓存、磁盘缓存和 Pickle 文件缓存。

## 安装

```bash
pip install farcache
```

要求 Python >= 3.10。

## 快速开始

```python
from farcache import lru_cache

@lru_cache
def fibonacci(n):
    if n < 2:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)

print(fibonacci(50))
```

## 内存缓存

基于 [cachebox](https://github.com/awolverp/cachebox) 实现，提供多种淘汰策略。所有装饰器都支持带括号和不带括号两种写法，并且原生支持 `async def` 函数。

包装后的函数暴露底层缓存对象：`f.cache` 可用于 `len(f.cache)` 查看条目数，`f.cache_clear()` 清空。

### cache

最简单的 LRU 缓存装饰器，默认 maxsize=1000。

```python
from farcache import cache

@cache
def add(a, b):
    return a + b
```

### lru_cache

**LRU (Least Recently Used)** — 淘汰最久未被访问的缓存条目。

```python
from farcache import lru_cache

@lru_cache(maxsize=500)
def query(sql):
    ...
```

### ttl_cache

**TTL (Time To Live)** — 缓存条目在超过指定时间后自动过期。

```python
from farcache import ttl_cache

@ttl_cache(maxsize=1000, ttl=300)  # 300 秒后过期
def get_config(key):
    ...
```

### vttl_cache

**VTTL (Virtual TTL)** — 与 TTL 类似，但采用惰性淘汰策略，仅在访问时检查并移除过期条目。

```python
from farcache import vttl_cache

@vttl_cache(maxsize=1000, ttl=60)
def get_status(service):
    ...
```

### lfu_cache

**LFU (Least Frequently Used)** — 淘汰访问次数最少的缓存条目。

```python
from farcache import lfu_cache

@lfu_cache(maxsize=1000)
def translate(word):
    ...
```

### fifo_cache

**FIFO (First In First Out)** — 淘汰最早进入缓存的条目。

```python
from farcache import fifo_cache

@fifo_cache(maxsize=1000)
def process(data):
    ...
```

### rr_cache

**RR (Random Replacement)** — 随机淘汰一个缓存条目。

```python
from farcache import rr_cache

@rr_cache(maxsize=1000)
def compute(x):
    ...
```

## 持久化缓存

`disk_cache` 与 `pkl_cache` 共享同一套语义：按**指定的参数**计算缓存键，把结果写到磁盘，跨进程和重启后依然有效。两者都支持 `async def` 函数。

### 选择要作为缓存键的参数

```python
from farcache import disk_cache

# 单个参数
@disk_cache(cache_key="query")
def search(query):
    ...

# 多个参数
@disk_cache(cache_key=["query", "top_k"])
def search(query, top_k=10):
    ...

# 全部参数（省略 cache_key）
@disk_cache()
def search(query, top_k=10, lang="zh"):
    ...
```

> **⚠️ 只有被列入 `cache_key` 的参数会参与缓存键的计算。**
> 未列入的参数即使改变，也会命中同一条缓存并返回旧结果——这是静默的错误结果，不会报错。
> 如果函数的输出依赖多个参数，请把它们全部列出，或直接省略 `cache_key` 以全部参数为键。

### 缓存键的稳定性

缓存键由参数值的**规范化编码**计算得出，而不是直接 pickle：

- 集合与字典会先排序再编码，因此不受 `PYTHONHASHSEED` 影响，跨进程稳定；
- 类型参与键的计算，`1`、`"1"`、`1.0`、`True` 互不冲突；
- 相等的值共享同一条缓存，`{"a": 1, "b": 2}` 与 `{"b": 2, "a": 1}` 命中同一项；
- 无法稳定序列化的值（文件句柄、锁、socket 等）会抛出 `UnstableKeyError`。
  在全参数模式下则降级为"不缓存"并打一条 warning 日志，不会中断调用。

### 运行时开关与跳过

```python
@disk_cache(cache_key="sql", is_cache="use_cache")
def run_query(sql, use_cache=True):
    ...

run_query("SELECT ...", use_cache=False)  # 跳过缓存，直接执行
```

`is_cache` 参数只控制是否读写缓存，**不参与缓存键**，因此关掉再打开仍会命中同一条目。

当 `cache_key` 指定的参数值为 `None` 时同样跳过缓存（全参数模式下不适用此规则）。

### 缓存控制 API

被装饰的函数附带一组缓存管理方法：

```python
@disk_cache(cache_key="query")
def search(query):
    ...

search.cache_key("python")  # 该次调用使用的键；跳过缓存时返回 None
search.cache_invalidate("python")  # 删除单条，返回是否存在
search.cache_clear()  # 清空，返回删除条数
search.cache_prune()  # 清理过期条目，返回删除条数
search.cache_close()  # 释放底层句柄，下次调用自动重开
search.__wrapped__  # 未被装饰的原函数
```

装饰器实例本身也可以作为上下文管理器，退出时关闭它开过的所有存储：

```python
with disk_cache(cache_key="query") as cached:
    @cached
    def search(query):
        ...
```

### disk_cache

基于 [diskcache](https://github.com/grantjenks/python-diskcache) 的 SQLite 存储，支持过期、容量上限和并发访问。**新项目优先选择它。**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `cache_key` | `str \| list[str] \| None` | `None` | 作为缓存键的参数名；`None` 表示全部参数 |
| `cache_dir` | `str \| None` | `None` | 缓存目录，为 None 时按函数标识自动生成 |
| `is_cache` | `str` | `"cache"` | 控制是否启用缓存的参数名 |
| `expire` | `float \| None` | `86400` | 过期时间（秒），`None` 表示永不过期 |
| `size_limit` | `int \| None` | `None` | 总字节数上限，由 diskcache 自行淘汰 |
| `**settings` | | | 其余参数透传给 `diskcache.Cache`（`eviction_policy`、`cull_limit` 等） |

```python
@disk_cache(cache_key="query", expire=3600, size_limit=512 * 1024 * 1024)
def search(query):
    ...
```

### pkl_cache

每条结果一个 `.pkl` 文件，按摘要前缀分片存放，采用临时文件 + `os.replace` 原子写入。适用于结果体积大、希望直接在文件系统里查看的场景。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `cache_key` | `str \| list[str] \| None` | `None` | 作为缓存键的参数名；`None` 表示全部参数 |
| `cache_dir` | `str` | `".cache"` | 存储 pkl 文件的目录 |
| `is_cache` | `str` | `"cache"` | 控制是否启用缓存的参数名 |
| `expire` | `float \| None` | `None` | 过期时间（秒），`None` 表示永不过期 |
| `max_entries` | `int \| None` | `None` | 条目数软上限，按写入时间淘汰最旧的 |
| `printf` | `bool` | `False` | 兼容选项，额外把缓存事件打到 stdout |

```python
@pkl_cache(cache_key="filepath", expire=7 * 86400, max_entries=10_000)
def parse_file(filepath):
    ...
```

`cache_dir` 的相对路径在**装饰时**解析为绝对路径，不受运行期 `os.chdir` 影响。

`cache_clear()` 只会删除自己写入的分片目录与 `.pkl` 文件，不会动缓存目录下的其他内容。

反序列化本身不适合处理不可信数据，只应使用当前用户可控的缓存目录。

### 日志

缓存命中与写入以 DEBUG 级别记录到 `farcache` logger：

```python
import logging

logging.getLogger("farcache").setLevel(logging.DEBUG)
```

## 其他

### cached_property

重新导出自标准库 `functools.cached_property`，将方法结果缓存为实例属性。

```python
from farcache import cached_property

class Config:
    @cached_property
    def settings(self):
        return load_settings()
```

## API 一览

| 装饰器 | 存储位置 | 淘汰策略 | 支持过期 | 支持 async |
|--------|---------|---------|---------|-----------|
| `cache` | 内存 | LRU | - | 是 |
| `lru_cache` | 内存 | LRU | - | 是 |
| `lfu_cache` | 内存 | LFU | - | 是 |
| `fifo_cache` | 内存 | FIFO | - | 是 |
| `rr_cache` | 内存 | 随机 | - | 是 |
| `ttl_cache` | 内存 | TTL | 是 | 是 |
| `vttl_cache` | 内存 | VTTL (惰性) | 是 | 是 |
| `disk_cache` | 磁盘 (SQLite) | 容量上限 | 是 | 是 |
| `pkl_cache` | 磁盘 (pkl) | 条目数上限 | 是 | 是 |
| `cached_property` | 实例属性 | - | - | - |

生成器函数（`yield`）无法被持久化装饰器缓存，装饰时会直接抛 `TypeError`；请改为返回列表。

## 从 1.x 升级

2.0 修正了若干会产出错误结果的问题，存在以下不兼容变更：

1. **缓存键算法变更。** 旧缓存不会被读取，首次运行相当于全部重算。旧的 `.cache` / `.disk_cache` 目录可以直接删除。
2. **Python 最低版本提升到 3.10。**
3. **`cache_key` 不再是必填参数**，省略时以全部参数为键。
4. **`vttl_cache` 的 `ttl` 之前从未生效**（被传给了构造函数，只对初始化数据有效），现已按每条目过期正确实现。
5. `PickleCache` / `DiskCache` 的内部结构重写，`_cache`、`_get_cache_file`、`_load_cache`、`_save_cache` 等私有成员已移除；公开的 `cache_clear()` 等方法取代了它们。

---

## 关于 farfarfun

[farfarfun](https://github.com/farfarfun) 是一个专注于实用工具库的开源组织，
涵盖云存储、数据处理、AI、多媒体与开发工具链等方向。

- 🏠 组织主页：<https://github.com/farfarfun>
- 📦 PyPI：<https://pypi.org/user/niuliangtao/>
- 📧 联系：farfarfun@qq.com

本项目基于 [MIT](LICENSE) 协议开源。
