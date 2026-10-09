# -*- coding: utf-8 -*-
"""
配置加载: 读取项目根目录 .env 文件

- KEY=VALUE 格式, 支持 # 注释行, 值可带引号
- 已存在的系统环境变量优先于 .env (便于临时覆盖)
- 通过 get(key, default) 取值
"""
import os

ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def _parse_env_file(path: str) -> dict:
    values = {}
    if not os.path.exists(path):
        return values
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if key:
                values[key] = value
    return values


_VALUES = _parse_env_file(ENV_FILE)


def get(key: str, default: str = "") -> str:
    """读取配置: 系统环境变量 > .env > default"""
    return os.environ.get(key) or _VALUES.get(key) or default


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(get(key, str(default)))
    except ValueError:
        return default


def get_float(key: str, default: float = 0.0) -> float:
    try:
        return float(get(key, str(default)))
    except ValueError:
        return default


def get_delay_range(min_key: str, max_key: str, default=(1.0, 2.0)):
    return (get_float(min_key, default[0]), get_float(max_key, default[1]))
