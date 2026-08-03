# nltcache

一个简洁的 Python 函数缓存装饰器库，提供多种缓存策略，涵盖内存缓存、磁盘缓存和 Pickle 文件缓存。

## 安装

```bash
pip install nltcache
```

要求 Python >= 3.8。

## 快速开始

```python
from nltcache import lru_cache

@lru_cache()
def fibonacci(n):
    if n < 2:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)

print(fibonacci(50))
```

## 内存缓存

基于 [cachebox](https://github.com/awolverp/cachebox) 实现，提供多种淘汰策略。

### cache

最简单的 LRU 缓存装饰器，默认 maxsize=1000，无需传参直接使用。

```python
from nltcache import cache

@cache
def add(a, b):
    return a + b
```

### lru_cache

**LRU (Least Recently Used)** — 淘汰最久未被访问的缓存条目。

```python
from nltcache import lru_cache

@lru_cache(maxsize=500)
def query(sql):
    ...
```

### ttl_cache

**TTL (Time To Live)** — 缓存条目在超过指定时间后自动过期。

```python
from nltcache import ttl_cache

@ttl_cache(maxsize=1000, ttl=300)  # 300 秒后过期
def get_config(key):
    ...
```

### vttl_cache

**VTTL (Virtual TTL)** — 与 TTL 类似，但采用惰性淘汰策略，仅在访问时检查并移除过期条目。

```python
from nltcache import vttl_cache

@vttl_cache(maxsize=1000, ttl=60)
def get_status(service):
    ...
```

### lfu_cache

**LFU (Least Frequently Used)** — 淘汰访问次数最少的缓存条目。

```python
from nltcache import lfu_cache

@lfu_cache(maxsize=1000)
def translate(word):
    ...
```

### fifo_cache

**FIFO (First In First Out)** — 淘汰最早进入缓存的条目。

```python
from nltcache import fifo_cache

@fifo_cache(maxsize=1000)
def process(data):
    ...
```

### rr_cache

**RR (Random Replacement)** — 随机淘汰一个缓存条目。

```python
from nltcache import rr_cache

@rr_cache(maxsize=1000)
def compute(x):
    ...
```

## 磁盘缓存

### disk_cache

基于 [diskcache](https://github.com/grantjenks/python-diskcache) 实现，将缓存持久化到本地磁盘，支持过期时间，适用于需要跨进程或重启后保留缓存的场景。

```python
from nltcache import disk_cache

@disk_cache(cache_key="query", expire=3600)  # 缓存 1 小时
def search(query):
    # 耗时的搜索操作
    ...

search("python cache")  # 首次执行，结果写入磁盘
search("python cache")  # 命中缓存，直接返回
```

**参数说明：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `cache_key` | `str` | *必填* | 用作缓存键的函数参数名 |
| `cache_dir` | `str \| None` | `None` | 缓存目录，为 None 时自动生成 |
| `is_cache` | `str` | `"cache"` | 控制是否启用缓存的布尔参数名 |
| `expire` | `int` | `86400` | 缓存过期时间（秒），默认 1 天 |

`cache_key` 必须是函数签名中的参数名。不同函数和不同类型的键会自动隔离；键为 `None` 时跳过缓存。

通过 `is_cache` 参数可以在运行时动态控制是否使用缓存：

```python
@disk_cache(cache_key="sql", is_cache="use_cache")
def run_query(sql, use_cache=True):
    ...

run_query("SELECT ...", use_cache=False)  # 跳过缓存，直接执行
```

## Pickle 文件缓存

### pkl_cache

将函数结果序列化为 `.pkl` 文件存储到本地，适用于需要简单持久缓存但不想引入额外数据库的场景。

```python
from nltcache import pkl_cache

@pkl_cache(cache_key="filepath", cache_dir=".my_cache")
def parse_file(filepath):
    # 耗时的文件解析操作
    ...

parse_file("/data/large.csv")  # 首次执行，结果写入 .pkl 文件
parse_file("/data/large.csv")  # 命中缓存
```

**参数说明：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `cache_key` | `str` | *必填* | 用作缓存键的函数参数名 |
| `cache_dir` | `str` | `".cache"` | 存储 pkl 文件的目录 |
| `is_cache` | `str` | `"cache"` | 控制是否启用缓存的布尔参数名 |
| `printf` | `bool` | `False` | 是否将缓存日志输出到 stdout |

Pickle 文件采用原子写入，但反序列化本身不适合不可信数据。只应使用当前用户可控的缓存目录；新项目优先选择支持过期和并发访问的 `disk_cache`。

## 其他

### cached_property

重新导出自标准库 `functools.cached_property`，将方法结果缓存为实例属性。

```python
from nltcache import cached_property

class Config:
    @cached_property
    def settings(self):
        # 仅在首次访问时执行
        return load_settings()
```

## API 一览

| 装饰器 | 存储位置 | 淘汰策略 | 支持过期 |
|--------|---------|---------|---------|
| `cache` | 内存 | LRU | - |
| `lru_cache` | 内存 | LRU | - |
| `lfu_cache` | 内存 | LFU | - |
| `fifo_cache` | 内存 | FIFO | - |
| `rr_cache` | 内存 | 随机 | - |
| `ttl_cache` | 内存 | TTL | 是 |
| `vttl_cache` | 内存 | VTTL (惰性) | 是 |
| `disk_cache` | 磁盘 | - | 是 |
| `pkl_cache` | 磁盘 (pkl) | - | - |
| `cached_property` | 实例属性 | - | - |
