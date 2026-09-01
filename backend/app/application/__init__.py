"""application 层（F 线重构目标骨架，F4/F5 逐批迁入）：用例编排——收参数、调领域引擎、返回结果。

由现 services 归位而来（chat/moment/character/system/group...）；ports/ 子目录放端口协议
（Protocol，不引 DI 框架）。api 只做 HTTP 收发，业务全委托本层。
"""
