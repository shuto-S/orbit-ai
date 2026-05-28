import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.ai.end_judge_agent import EndJudgeAgent
from app.ai.response_agent import ResponseAgent
from app.latency import DISABLED_LATENCY_LOGGER, LatencyLogger
from app.memory.extractor import MemoryExtractor
from app.memory.retriever import MemoryRetriever
from app.memory.store import MemoryStore
from app.memory.summarizer import SessionSummarizer
from app.session.end_detector import EndDetector
from app.session.proactive_policy import ProactiveDecision, ProactivePolicy
from app.session.state import SessionState

GREETING_TERMS = (
    "こんにちは",
    "こんばんは",
    "こんばんわ",
    "おはよう",
    "hello",
    "hi",
)

WAKE_STRIP_CHARS = " 、,。.!！?？\t"


@dataclass(frozen=True)
class SessionOutput:
    text: str | None
    state: SessionState
    session_id: str | None


class SessionManager:
    def __init__(
        self,
        profile: dict[str, Any],
        proactive_config: dict[str, Any],
        store: MemoryStore,
        response_agent: ResponseAgent | None = None,
        latency: LatencyLogger | None = None,
    ) -> None:
        self.profile = profile
        self.store = store
        self.state = SessionState.IDLE
        self.session_id: str | None = None
        self.idle_since = datetime.now(UTC)
        self.last_confirmation_text = ""
        self.pending_proactive_text = ""
        self.latency = latency or DISABLED_LATENCY_LOGGER
        self.response_agent = response_agent or ResponseAgent(model=self._assistant_model(), latency=self.latency)
        self.retriever = MemoryRetriever(store)
        self.end_detector = EndDetector()
        self.end_judge = EndJudgeAgent(self.end_detector)
        self.summarizer = SessionSummarizer()
        self.extractor = MemoryExtractor()
        self.proactive_policy = ProactivePolicy(proactive_config, store)

    @property
    def assistant_display_name(self) -> str:
        assistant = self.profile.get("assistant", {})
        return str(assistant.get("display_name") or assistant.get("name") or "AI")

    @property
    def wake_words(self) -> list[str]:
        assistant = self.profile.get("assistant", {})
        words = assistant.get("wake_words", [])
        if isinstance(words, list):
            return [str(word) for word in words if str(word).strip()]
        return [self.assistant_display_name]

    def _assistant_model(self) -> str:
        assistant = self.profile.get("assistant", {})
        model = assistant.get("model")
        return str(model) if model else ""

    def reset(self) -> SessionOutput:
        self.state = SessionState.IDLE
        self.session_id = None
        self.last_confirmation_text = ""
        self.pending_proactive_text = ""
        self.idle_since = datetime.now(UTC)
        return SessionOutput("現在のセッションを破棄して待機状態に戻りました。", self.state, self.session_id)

    def handle_input(self, text: str) -> SessionOutput:
        user_text = text.strip()
        if self.state == SessionState.IDLE:
            return self._handle_idle(user_text)
        if self.state == SessionState.PROACTIVE_PERMISSION_CHECK:
            return self._handle_proactive_permission(user_text)
        if self.state == SessionState.CONFIRMING_END:
            return self._handle_end_confirmation(user_text)
        return self._handle_conversation_turn(user_text)

    def check_proactive(self) -> ProactiveDecision:
        return self.proactive_policy.evaluate(self.idle_since)

    def start_proactive_permission(self, permission_text: str) -> SessionOutput:
        self.pending_proactive_text = permission_text
        self.state = SessionState.PROACTIVE_PERMISSION_CHECK
        self.store.add_proactive_event(permission_text, outcome="proposed")
        return SessionOutput(permission_text, self.state, self.session_id)

    def _handle_idle(self, user_text: str) -> SessionOutput:
        stripped = self._strip_wake_word(user_text)
        if stripped is None:
            return SessionOutput(None, self.state, self.session_id)

        self.state = SessionState.WAKE_DETECTED
        self._start_session()
        if not stripped:
            assistant_text = "はい、聞いています。"
            self.store.add_message(self.session_id_or_raise(), "assistant", assistant_text)
            self.state = SessionState.WAITING_FOR_NEXT_TURN
            return SessionOutput(assistant_text, self.state, self.session_id)
        if self._is_wake_greeting(stripped):
            assistant_text = self._greeting_response(stripped)
            session_id = self.session_id_or_raise()
            self.store.add_message(session_id, "user", stripped)
            self.store.add_message(session_id, "assistant", assistant_text)
            self.state = SessionState.WAITING_FOR_NEXT_TURN
            return SessionOutput(assistant_text, self.state, self.session_id)
        return self._process_user_text(stripped)

    def _handle_conversation_turn(self, user_text: str) -> SessionOutput:
        detection = self.end_judge.judge(user_text)
        if detection.end_candidate:
            self.store.add_message(self.session_id_or_raise(), "user", user_text)
            self.last_confirmation_text = detection.confirmation_text
            self.state = SessionState.CONFIRMING_END
            return SessionOutput(detection.confirmation_text, self.state, self.session_id)
        return self._process_user_text(user_text)

    def _handle_end_confirmation(self, user_text: str) -> SessionOutput:
        if not user_text:
            return SessionOutput(self.last_confirmation_text or "ここで終わりにしますか？", self.state, self.session_id)
        if self.end_detector.is_affirmative(user_text) and not self.end_detector.is_negative(user_text):
            self.store.add_message(self.session_id_or_raise(), "user", user_text)
            return self._close_session()
        if user_text:
            self.store.add_message(self.session_id_or_raise(), "user", user_text)
        self.state = SessionState.WAITING_FOR_NEXT_TURN
        if self.end_detector.is_negative(user_text):
            return SessionOutput("わかりました。続けましょう。", self.state, self.session_id)
        return SessionOutput(
            "続ける前提で受け取ります。次に話したいことを教えてください。", self.state, self.session_id
        )

    def _handle_proactive_permission(self, user_text: str) -> SessionOutput:
        if self.end_detector.is_affirmative(user_text) and not self.end_detector.is_negative(user_text):
            self.store.add_proactive_event(self.pending_proactive_text, outcome="accepted", user_response=user_text)
            self._start_session()
            self.store.add_message(self.session_id_or_raise(), "user", user_text)
            assistant_text = (
                "ありがとうございます。では、未完了の論点から短く整理します。どこまで決めるか確認したいです。"
            )
            self.store.add_message(self.session_id_or_raise(), "assistant", assistant_text)
            self.state = SessionState.WAITING_FOR_NEXT_TURN
            return SessionOutput(assistant_text, self.state, self.session_id)
        self.store.add_proactive_event(self.pending_proactive_text, outcome="rejected", user_response=user_text)
        self.state = SessionState.IDLE
        self.session_id = None
        self.idle_since = datetime.now(UTC)
        return SessionOutput("わかりました。また必要なときに呼んでください。", self.state, self.session_id)

    def _process_user_text(self, user_text: str) -> SessionOutput:
        session_id = self.session_id_or_raise()
        self.state = SessionState.LISTENING
        self.store.add_message(session_id, "user", user_text)
        self.state = SessionState.THINKING
        memories = self.retriever.relevant(user_text)
        recent = self.store.get_recent_messages(session_id)
        assistant_text = self.response_agent.respond(
            profile=self.profile,
            memories=memories,
            session_state=self.state.value,
            recent_messages=recent,
            user_text=user_text,
            session_id=session_id,
            store=self.store,
        )
        self.state = SessionState.SPEAKING
        self.store.add_message(session_id, "assistant", assistant_text)
        self.state = SessionState.WAITING_FOR_NEXT_TURN
        return SessionOutput(assistant_text, self.state, self.session_id)

    def _close_session(self) -> SessionOutput:
        session_id = self.session_id_or_raise()
        self.state = SessionState.CLOSING
        messages = self.store.get_session_messages(session_id)
        summary = self.summarizer.summarize(messages)
        self.store.add_summary(
            session_id=session_id,
            summary=str(summary["summary"]),
            open_loops=list(summary["open_loops"]),
            decisions=list(summary["decisions"]),
            follow_up_candidates=list(summary["follow_up_candidates"]),
        )
        self.store.add_tasks_from_summary(
            session_id=session_id,
            open_loops=list(summary["open_loops"]),
            follow_up_candidates=list(summary["follow_up_candidates"]),
        )
        for memory in self.extractor.extract(messages):
            self.store.add_memory(memory.kind, memory.content, memory.priority, memory.confidence)
        assistant_text = "わかりました。また呼んでください。"
        self.store.add_message(session_id, "assistant", assistant_text)
        self.session_id = None
        self.state = SessionState.IDLE
        self.idle_since = datetime.now(UTC)
        return SessionOutput(assistant_text, self.state, self.session_id)

    def _start_session(self) -> None:
        self.session_id = str(uuid.uuid4())
        self.latency.bind_session(self.session_id)
        self.idle_since = None

    def _strip_wake_word(self, text: str) -> str | None:
        normalized_text = self._normalize_wake_text(text)
        for word in sorted(self.wake_words, key=len, reverse=True):
            normalized_word = self._normalize_wake_text(word)
            if not normalized_word:
                continue
            index = normalized_text.find(normalized_word)
            if index >= 0:
                start = self._normalized_index_to_original(text, index)
                end = self._normalized_index_to_original(text, index + len(normalized_word))
                stripped = text[:start] + text[end:]
                return stripped.strip(WAKE_STRIP_CHARS)
        return None

    @staticmethod
    def _normalize_wake_text(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text).lower()
        return "".join(SessionManager._hiragana_to_katakana(char) for char in normalized if not char.isspace())

    @staticmethod
    def _hiragana_to_katakana(char: str) -> str:
        codepoint = ord(char)
        if 0x3041 <= codepoint <= 0x3096:
            return chr(codepoint + 0x60)
        return char

    @classmethod
    def _normalized_index_to_original(cls, text: str, target_index: int) -> int:
        normalized_length = 0
        for index, char in enumerate(text):
            if normalized_length == target_index:
                return index
            normalized_length += len(cls._normalize_wake_text(char))
            if normalized_length > target_index:
                return index + 1
        return len(text)

    @staticmethod
    def _is_wake_greeting(text: str) -> bool:
        normalized = text.strip().lower()
        return any(term in normalized for term in GREETING_TERMS)

    @staticmethod
    def _greeting_response(text: str) -> str:
        normalized = text.strip().lower()
        if "おはよう" in normalized:
            return "おはようございます。"
        if "こんばん" in normalized:
            return "こんばんは。"
        if "こんにちは" in normalized:
            return "こんにちは。"
        return "はい、聞いています。"

    def session_id_or_raise(self) -> str:
        if not self.session_id:
            raise RuntimeError("session_id is not set")
        return self.session_id
