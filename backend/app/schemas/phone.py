"""手机感知 API 请求/响应模型"""
from pydantic import BaseModel


class AutoReportItem(BaseModel):
    """手机端主动上报的单条通知"""
    app: str = ""
    package: str = ""
    title: str = ""
    text: str = ""
    time: str = ""


class AutoReportRequest(BaseModel):
    notifications: list[AutoReportItem] = []
