from dataclasses import dataclass


@dataclass(frozen=True)
class ProactiveCandidate:
    should_speak: bool
    priority: float
    permission_text: str
    reason: str
    topic: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    summary: str | None = None
    suggested_next_step: str | None = None
    accepted_prompt: str | None = None


class ProactiveAgent:
    def build_candidate(
        self,
        open_loops: list[str],
        contexts: list[dict[str, str | None]] | None = None,
    ) -> ProactiveCandidate:
        if not open_loops and not contexts:
            return ProactiveCandidate(False, 0.0, "", "no_open_loops")
        context = contexts[0] if contexts else {}
        topic = context.get("topic") or open_loops[0]
        text = "さっきの件で、1つ確認したいことがあります。今話してもいいですか？"
        if len(topic) <= 40:
            text = f"さっきの「{topic}」の続きで、1つ確認したいことがあります。今話してもいいですか？"
        return ProactiveCandidate(
            True,
            0.7,
            text,
            "open_loop",
            topic=topic,
            source_type=context.get("source_type"),
            source_id=context.get("source_id"),
            summary=context.get("summary"),
            suggested_next_step=context.get("suggested_next_step"),
            accepted_prompt=context.get("accepted_prompt"),
        )
