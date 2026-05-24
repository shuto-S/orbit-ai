from dataclasses import dataclass


@dataclass(frozen=True)
class ProactiveCandidate:
    should_speak: bool
    priority: float
    permission_text: str
    reason: str


class ProactiveAgent:
    def build_candidate(self, open_loops: list[str]) -> ProactiveCandidate:
        if not open_loops:
            return ProactiveCandidate(False, 0.0, "", "no_open_loops")
        topic = open_loops[0]
        text = "さっきの件で、1つ確認したいことがあります。今話してもいいですか？"
        if len(topic) <= 40:
            text = f"さっきの「{topic}」の続きで、1つ確認したいことがあります。今話してもいいですか？"
        return ProactiveCandidate(True, 0.7, text, "open_loop")
