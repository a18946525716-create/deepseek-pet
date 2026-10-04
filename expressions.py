"""Pick the pet's expression from what the conversation is about.

Deliberately simple: keyword scoring over Chinese and English, plus a few emoji.
A weighted vote, so "哈哈太谢谢你了" lands on laugh rather than a coin flip.

    from expressions import classify
    classify("谢谢你，今天真开心")   -> "happy"
"""

from __future__ import annotations

# Moods the skin can render; keys not present in a skin fall back to neutral.
MOOD_LABELS = {
    "neutral": "平静",
    "happy": "开心",
    "laugh": "大笑",
    "smile": "微笑",
    "surprised": "惊讶",
    "sad": "难过",
    "angry": "生气",
    "shy": "害羞",
    "think": "思考",
    "talk": "说话",
}
MOODS = list(MOOD_LABELS)

# (mood, weight, keywords) — stronger signals first, so ties resolve sensibly.
RULES: list[tuple[str, float, tuple[str, ...]]] = [
    ("laugh", 3.0, ("哈哈", "笑死", "笑不活", "233", "lol", "lmao", "🤣", "😂", "xd")),
    ("shy", 3.0, ("害羞", "不好意思", "脸红", "羞", "讨厌啦", "///", "😳", "🙈")),
    ("happy", 2.2, ("谢谢", "感谢", "开心", "高兴", "太好了", "好棒", "真棒", "棒", "赞",
                    "喜欢", "爱你", "真好", "满意", "舒服", "干得漂亮", "厉害", "❤", "🥰",
                    "😊", "😄", "😁", "🙂", "✨",
                    "thanks", "thank you", "happy", "great", "awesome", "love", "nice", "good job")),
    ("sad", 2.6, ("难过", "伤心", "不开心", "郁闷", "想哭", "哭了", "好累", "辛苦", "孤独",
                  "失望", "委屈", "抱抱", "😢", "😭", "🥺", "sad", "tired", "depressed", "lonely")),
    ("angry", 2.6, ("生气", "讨厌", "好烦", "烦死", "气死", "可恶", "无语", "过分", "垃圾",
                    "angry", "annoying", "hate", "terrible", "😠", "😡")),
    ("surprised", 2.4, ("哇", "天哪", "天呐", "居然", "竟然", "真的吗", "震惊", "没想到",
                        "wow", "really", "omg", "😮", "😲", "🤯")),
    ("think", 1.0, ("？", "?", "怎么", "为什么", "如何", "能不能", "可以吗", "是什么", "多少",
                    "帮我", "请教", "how", "why", "what", "which", "explain")),
]


def classify(text: str, allow_think: bool = True) -> str:
    """Best-matching mood for `text`, or "neutral" when nothing stands out."""
    if not text:
        return "neutral"
    low = text.lower()
    best, best_score = "neutral", 0.0
    for mood, weight, keywords in RULES:
        if mood == "think" and not allow_think:
            continue
        hits = sum(1 for kw in keywords if kw in low)
        if not hits:
            continue
        score = weight * min(hits, 2)
        if score > best_score:
            best, best_score = mood, score
    return best
