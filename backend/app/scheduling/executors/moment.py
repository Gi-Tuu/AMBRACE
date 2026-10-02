"""朋友圈执行器 — moment_publish / moment_comment（A20 批 4a）

自 ``arbiter._execute`` 逐字节搬入的两条分支（2026-10-02，原为梯子末尾的 ``elif``）。
发布成功才补评论；补评论失败只降级为 warning，不影响本轮判定（return True）。

依赖纪律沿用前几批：``publish_moment`` / ``generate_comments_for_moment`` /
``generate_pending_comments`` 原本就是分支体内的局部 import（tests/ 打的是各自模块属性），
照原样留在函数内。logger 名故意保留 ``scheduler.arbiter``（D-1）。
"""
from app.scheduling.executors.context import GateBundle
from app.scheduling.executors.registry import handler
from app.utils.logger import get_logger

_logger = get_logger("scheduler.arbiter")


@handler("moment_publish")
async def run_moment_publish_exec(item: dict, candidate: dict, char_id: int, g: GateBundle) -> bool:
    from app.scheduling.moment_publisher import publish_moment
    result = await publish_moment(char_id, skip_interval=False)
    if result is not None:
        from app.application.moment_service import generate_comments_for_moment
        try:
            await generate_comments_for_moment(result["id"])
        except Exception as e:
            _logger.warning("comments after publish failed: %s", e)
        return True
    return False


@handler("moment_comment")
async def run_moment_comment_exec(item: dict, candidate: dict, char_id: int, g: GateBundle) -> bool:
    from app.application.moment_service import generate_pending_comments
    await generate_pending_comments()
    return True
