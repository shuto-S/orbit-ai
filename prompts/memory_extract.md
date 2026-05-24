You extract long-term useful memories from a conversation.

## Extract Only

- User preferences
- Work style
- Current projects
- Decisions
- Open issues
- Follow-up candidates
- Preferences for interacting with the AI

## Do Not Extract

- Information that is too temporary
- Unimportant small talk
- Mere guesses
- Sensitive personal information
- Duplicates of already saved information

## Conversation

{{session_messages}}

## JSON Output

Return JSON only.

{
  "memories": [
    {
      "kind": "preference",
      "content": "",
      "priority": 0.5,
      "confidence": 0.8
    }
  ]
}
