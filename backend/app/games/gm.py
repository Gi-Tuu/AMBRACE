"""系统主持人：纯模板文案，不调 LLM。

多人游戏需要 GM 时由引擎调用；GM 不占角色位，前端特殊渲染（系统消息样式）。
零 LLM 保证：任何阶段文案都来自本地的模板字典，不经过模型。
"""
from __future__ import annotations


def gm_announce(game_type: str, phase: str, **kw) -> str:
    templates = {
        "undercover": {
            "start": "🎮 谁是卧底开始！请查看各自的词语。",
            "describe": f"第{kw.get('round', 1)}轮描述阶段，请按座次依次描述你的词语（不能直接说词）。",
            "vote": "描述完毕，请投票选出你认为的卧底。",
            "eliminated": f"{kw.get('name', '')}被淘汰了，TA的词是「{kw.get('word', '')}」，身份是{kw.get('role', '')}。",
            "win_civilians": "🎉 卧底全部出局，平民获胜！",
            "win_undercover": "🕵️ 卧底存活到了最后，卧底获胜！",
            "draw": "🤝 平局，没有人获胜。",
        },
        "truth_or_dare": {
            "start": f"🎭 真心话大冒险开始！{kw.get('p1_name', '')}先来。",
            "choose_truth": f"{kw.get('name', '')}选择了真心话。",
            "choose_dare": f"{kw.get('name', '')}选择了大冒险。",
            "give_truth": f"{kw.get('name', '')}给{kw.get('target', '')}出一道真心话：{kw.get('question', '')}",
            "give_dare": f"{kw.get('name', '')}给{kw.get('target', '')}一个任务：{kw.get('task', '')}",
            "answer": f"{kw.get('name', '')}回答：{kw.get('content', '')}",
            "penalty": f"{kw.get('name', '')}接受了惩罚，扣 {kw.get('penalty', 2)} 分。",
            "win": f"{kw.get('name', '')}赢得了本局！",
        },
        "twenty_q": {
            "start": f"🔍 猜词20问开始！{kw.get('thinker_name', '')}想了一个词，{kw.get('guesser_name', '')}来猜。",
            "ask": f"第{kw.get('n', 1)}问：{kw.get('question', '')}",
            "answer": f"{kw.get('name', '')}回答：{kw.get('answer', '')}",
            "guess": f"{kw.get('name', '')}猜是「{kw.get('word', '')}」。",
            "guess_right": "🎉 猜对了！",
            "guess_wrong": "❌ 猜错了。",
            "win_guesser": "🎉 猜中了，猜方获胜！",
            "win_thinker": "🏆 猜方没猜中，想词方获胜！",
        },
        "werewolf": {
            "night": "🌙 天黑请闭眼…",
            "day": f"☀️ 天亮了。昨晚{kw.get('victim', '无人')}倒下了。",
            "vote": "请投票。",
        },
    }
    return templates.get(game_type, {}).get(phase, "")
