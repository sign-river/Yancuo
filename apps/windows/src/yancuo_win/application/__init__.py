"""应用服务层（阶段 A：仅启动编排）。"""

from yancuo_win.application.bootstrap import RuntimeContext, bootstrap_runtime

__all__ = ["RuntimeContext", "bootstrap_runtime"]
