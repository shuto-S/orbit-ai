# Scenario Fixtures

Scenario replay fixtures live in `tests/fixtures/scenarios/*.json`.
They build a temporary `MemoryStore`, evaluate `ProactivePolicy` with a fixed `now`, and fail through pytest assertions when the replayed decision differs from `expected`.

## Format

```json
{
  "name": "open task after idle should prompt",
  "now": "2026-05-28T10:00:00+00:00",
  "idle_since": "2026-05-28T09:55:00+00:00",
  "tasks": [
    {
      "title": "請求書の確認",
      "status": "open",
      "priority": 0.7,
      "source": "open_loop",
      "source_session_id": "previous"
    }
  ],
  "summaries": [
    {
      "session_id": "previous",
      "summary": "前回の要約",
      "open_loops": ["請求書の確認"],
      "decisions": [],
      "follow_up_candidates": []
    }
  ],
  "recent_proactive_events": [
    {
      "proposed_text": "さっきの件で、今話してもいいですか？",
      "outcome": "rejected",
      "user_response": "今は無理",
      "created_at": "2026-05-28T09:45:00+00:00"
    }
  ],
  "expected": {
    "allowed": true,
    "reason": "open_loop",
    "candidate_contains": "請求書",
    "selected_open_loops": ["請求書の確認"],
    "daily_review_candidate": "請求書の確認"
  }
}
```

- `now`: ISO 8601 datetime passed to `ProactivePolicy.evaluate(..., now=...)`.
- `idle_since`: ISO 8601 datetime or `null`.
- `tasks`: optional task rows. `status` must be `open`, `done`, `snoozed`, or `cancelled`.
- `summaries`: optional previous session summaries used by `MemoryStore.latest_open_loops()`.
- `recent_proactive_events`: optional proactive event rows with explicit `created_at`.
- `expected.allowed`: required proactive allow/deny expectation.
- `expected.reason`: optional exact `ProactiveDecision.reason`.
- `expected.candidate_contains`: optional substring expected in permission text.
- `expected.selected_open_loops`: optional exact task/open-loop selection expectation.
- `expected.daily_review_candidate`: optional first selected open loop, or `null` when there is no review candidate.
