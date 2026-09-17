# -*- coding: utf-8 -*-
"""stdio / 网关重定向日志的「启动前轮转」。

server_stderr.log / server_stdout.log / gateway_*.log 是进程级 append 重定向，
不经过 logging，无按天轮转，会无限增长（实测 server_stderr.log 一度 93.6MB）。
在「打开重定向句柄之前」调用 rotate_stdio_log()：超阈值就把旧文件滚动为
<path>.1 .. <path>.keep，随后 open(path,'a') 会自动重建新空文件。

已知边界：Windows 上被占用的文件无法改名，因此只能在进程启动前切；一个长时间
运行的实例在两次重启之间仍可能超过阈值，属预期行为，不要尝试运行中强切。
"""
import os


def rotate_stdio_log(path: str, max_mb: int = 10, keep: int = 2) -> str:
    """若 path 体积 >= max_mb，滚动为 path.1 .. path.keep（覆盖最老）。返回动作描述。"""
    try:
        if not os.path.isfile(path):
            return ""
        if os.path.getsize(path) < max_mb * 1024 * 1024:
            return ""
        for i in range(keep, 0, -1):
            cur = f"{path}.{i}"
            if i == keep:
                if os.path.exists(cur):
                    os.remove(cur)
                continue
            if os.path.exists(cur):
                os.replace(cur, f"{path}.{i + 1}")
        os.replace(path, f"{path}.1")
        return f"日志轮转：{os.path.basename(path)} -> .1（阈值 {max_mb}MB，保留 {keep} 份）"
    except OSError:
        # 旧进程仍持有句柄 / 权限问题：本次不轮转，保证启动流程不受影响
        return ""
