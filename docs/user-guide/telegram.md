# Telegram usage

Gemma Agent is a **Telegram bot**. Write in natural language; slash commands are optional.

## Reliable tasks

| Task | Example |
|------|---------|
| Translation | «translate to English: hello» |
| Math | «calculate 23+45*45», «15% of 2500» |
| Reminders | «remind me at 22:50 to sleep» |
| Weather | «weather in Minsk» |
| News | «latest news» (needs SearXNG) |

Reminders fire within a few minutes after the scheduled time (background poll).

## Timezone

Set city in `/me` or bot uses `Europe/Moscow` unless configured. You can say «at 12:00 utc» in the phrase.

## Images

| Method | Example |
|--------|---------|
| Text only | «generate image: sunset over mountains» or `/imagine cyberpunk city` |
| Photo + caption | One message: photo + «redraw in anime style» |
| Vision describe | «what is in this photo?» (not image generation) |

Daily image quota may apply — bot will say when exhausted.

## Feedback

👍 / 👎 or `/rate +1` / `/rate -1` — used for ephemeral learning patches (admin-approved on prod).

## Commands

`/help` — command list  
`/forget` — drop a stored fact  
`/me` — profile and timezone

Full admin list: [Admin & ops](admin-ops.md)
