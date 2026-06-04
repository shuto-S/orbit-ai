# Turn Analysis

Analyze the latest user turn and assistant response for future assistant behavior.

Return strict JSON only. Do not include markdown, code fences, commentary, or extra keys.

Required shape:

{
  "task_candidates": [],
  "memory_candidates": [],
  "open_loop_candidates": [],
  "follow_up_candidates": [],
  "permission_required_actions": []
}

Rules:

- Do not invent tasks, memories, or follow-ups.
- Do not store secrets, API keys, passwords, access tokens, private keys, or credentials.
- Mark uncertain items with low confidence.
- Use `needs_confirmation=true` when the user did not explicitly ask Orbit to remember or create something.
- Keep Japanese text as Japanese.
- Return empty arrays when there is nothing useful.
- Keep each title short and concrete.

Task candidate fields:

- title: string
- due_text: string or null
- confidence: number from 0 to 1
- needs_confirmation: boolean
- source_text: string

Memory candidate fields:

- content: string
- kind: one of preference, profile, project, decision, open_loop, relationship, manual
- confidence: number from 0 to 1
- sensitivity: normal or sensitive
- needs_confirmation: boolean
- source_text: string

Open loop candidate fields:

- title: string
- summary: string
- suggested_next_step: string or null
- confidence: number from 0 to 1
- source_text: string

Follow-up candidate fields:

- text: string
- due_text: string or null
- reason: string
- confidence: number from 0 to 1

Permission-required action fields may be any JSON object, but only include actions that should not run without user confirmation.

Latest user turn:

{{user_text}}

Assistant response:

{{assistant_text}}
