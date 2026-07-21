"""兼容 `yancuo` console_script 与 `python -m yancuo_win`。"""

from yancuo_win.__main__ import main

__all__ = ["main"]
