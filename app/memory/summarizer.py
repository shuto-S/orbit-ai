from app.memory.store import Message


class SessionSummarizer:
    def summarize(self, messages: list[Message]) -> dict[str, list[str] | str]:
        user_messages = [message.content for message in messages if message.role == "user"]
        assistant_messages = [message.content for message in messages if message.role == "assistant"]
        if user_messages:
            summary = " / ".join(user_messages[-3:])
        elif assistant_messages:
            summary = " / ".join(assistant_messages[-2:])
        else:
            summary = "短い会話セッション。"

        joined = "\n".join(user_messages)
        open_loops: list[str] = []
        follow_ups: list[str] = []
        decisions: list[str] = []

        for text in user_messages:
            if any(keyword in text for keyword in ("あとで", "後で", "未定", "検討", "確認したい")):
                open_loops.append(text)
            if any(keyword in text for keyword in ("それで進める", "決定", "これでいく")):
                decisions.append(text)
            if any(keyword in text for keyword in ("フォロー", "続き", "また")):
                follow_ups.append(text)

        if "あとで" in joined or "後で" in joined:
            follow_ups.append("ユーザーが後で扱う意向を示した話題を確認する。")

        return {
            "summary": summary,
            "decisions": self._unique(decisions),
            "open_loops": self._unique(open_loops),
            "follow_up_candidates": self._unique(follow_ups),
        }

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                result.append(value)
        return result
