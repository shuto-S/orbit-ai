from app.session.end_detector import EndDetection, EndDetector


class EndJudgeAgent:
    def __init__(self, detector: EndDetector | None = None) -> None:
        self.detector = detector or EndDetector()

    def judge(self, user_text: str, _assistant_text: str = "") -> EndDetection:
        return self.detector.detect(user_text)
