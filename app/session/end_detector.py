from dataclasses import dataclass

END_CANDIDATE_TERMS = (
    "ありがとう",
    "ありがと",
    "大丈夫",
    "いったんここまで",
    "一旦ここまで",
    "またあとで",
    "また後で",
    "以上",
    "助かった",
    "了解",
    "それで進める",
    "終わり",
    "終了",
    "ここまで",
)

AFFIRMATIVE_TERMS = (
    "うん",
    "はい",
    "ok",
    "okay",
    "お願い",
    "終わりで",
    "ここまでで",
    "大丈夫",
    "いいよ",
    "戻って",
)

NEGATIVE_TERMS = (
    "いや",
    "まだ",
    "続けて",
    "もう一つ",
    "もうひとつ",
    "追加で",
    "それと",
    "終わらない",
)


@dataclass(frozen=True)
class EndDetection:
    end_candidate: bool
    confidence: float
    confirmation_text: str
    reason: str


class EndDetector:
    def detect(self, user_text: str) -> EndDetection:
        normalized = user_text.strip().lower()
        for term in END_CANDIDATE_TERMS:
            if term.lower() in normalized:
                return EndDetection(
                    end_candidate=True,
                    confidence=0.8,
                    confirmation_text="この件はいったんここまでにしますか？",
                    reason=f"終了候補表現を検出: {term}",
                )
        return EndDetection(False, 0.0, "", "終了候補なし")

    def is_affirmative(self, user_text: str) -> bool:
        normalized = user_text.strip().lower()
        return any(term.lower() in normalized for term in AFFIRMATIVE_TERMS)

    def is_negative(self, user_text: str) -> bool:
        normalized = user_text.strip().lower()
        return any(term.lower() in normalized for term in NEGATIVE_TERMS)
