"""infra 层（F 线重构目标骨架，F1 逐批迁入）：技术设施——db / mcp / voice / plugins / push / llm / events。

只被 domain/application/api 依赖，自身不依赖业务层。迁移期旧路径保留薄壳 re-export。
"""
