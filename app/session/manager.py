import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.ai.end_judge_agent import EndJudgeAgent
from app.ai.response_agent import ResponseAgent
from app.config.autonomy import AutonomyConfig
from app.latency import DISABLED_LATENCY_LOGGER, LatencyLogger
from app.memory.extractor import MemoryExtractor
from app.memory.retriever import MemoryRetriever
from app.memory.store import MemoryStore
from app.memory.summarizer import SessionSummarizer
from app.session.end_detector import EndDetector
from app.session.lifecycle import close_session
from app.session.proactive_policy import ProactiveDecision, ProactivePolicy
from app.session.state import SessionState
from app.session.wake import greeting_response, is_wake_greeting, strip_wake_word


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
        autonomy_config: AutonomyConfig | None = None,
        response_agent: ResponseAgent | None = None,
        latency: LatencyLogger | None = None,
        start_without_wake_word: bool = False,
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
        self.autonomy_config = autonomy_config or AutonomyConfig()
        self.proactive_policy = ProactivePolicy(proactive_config, store, autonomy=self.autonomy_config)
        self._start_without_wake_word_available = start_without_wake_word

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

    def check_proactive(self, trigger: str = "direct") -> ProactiveDecision:
        decision = self.proactive_policy.evaluate(self.idle_since)
        self.store.add_decision_log(
            kind="proactive_check",
            session_id=self.session_id,
            candidate_text=decision.candidate.permission_text or None,
            decision="ask_permission" if decision.allowed else "deny",
            reason=decision.reason,
            score=decision.candidate.priority,
            metadata={
                "trigger": trigger,
                "state": self.state.value,
                "has_idle_since": self.idle_since is not None,
                "candidate_should_speak": decision.candidate.should_speak,
            },
        )
        return decision

    def start_proactive_permission(self, permission_text: str) -> SessionOutput:
        self.pending_proactive_text = permission_text
        self.state = SessionState.PROACTIVE_PERMISSION_CHECK
        self.store.add_proactive_event(permission_text, outcome="proposed")
        return SessionOutput(permission_text, self.state, self.session_id)

    def start_conversation(self, assistant_text: str = "こんにちは。何から始めますか？") -> SessionOutput:
        if self.state != SessionState.IDLE:
            return SessionOutput(None, self.state, self.session_id)
        self._start_session()
        greeting = assistant_text.strip() or "はい、聞いています。"
        self.store.add_message(self.session_id_or_raise(), "assistant", greeting)
        self.state = SessionState.WAITING_FOR_NEXT_TURN
        return SessionOutput(greeting, self.state, self.session_id)

    def _handle_idle(self, user_text: str) -> SessionOutput:
        stripped = strip_wake_word(user_text, self.wake_words)
        if stripped is None:
            if not user_text or not self._start_without_wake_word_available:
                return SessionOutput(None, self.state, self.session_id)
            stripped = user_text

        self.state = SessionState.WAKE_DETECTED
        self._start_session()
        if not stripped:
            assistant_text = "はい、聞いています。"
            self.store.add_message(self.session_id_or_raise(), "assistant", assistant_text)
            self.state = SessionState.WAITING_FOR_NEXT_TURN
            return SessionOutput(assistant_text, self.state, self.session_id)
        if is_wake_greeting(stripped):
            assistant_text = greeting_response(stripped)
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
        assistant_text = close_session(self.store, session_id, self.summarizer, self.extractor)
        self.session_id = None
        self.state = SessionState.IDLE
        self.idle_since = datetime.now(UTC)
        return SessionOutput(assistant_text, self.state, self.session_id)

    def _start_session(self) -> None:
        self._start_without_wake_word_available = False
        self.session_id = str(uuid.uuid4())
        self.latency.bind_session(self.session_id)
        self.idle_since = None

    def session_id_or_raise(self) -> str:
        if not self.session_id:
            raise RuntimeError("session_id is not set")
        return self.session_id
