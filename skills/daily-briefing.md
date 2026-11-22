---
name: daily-briefing
description: Compose a compact morning briefing from the clock, weather, goals, reminders, memory, and headlines
---

# Skill: Daily Briefing

Compose the user's morning briefing. Be compact — this is a 60-second read.

1. **Time and weather.** `get_datetime` for today, then `weather` for the
   user's city (memory preference "city" if set, else Chennai).
2. **Commitments.** `list_goals` for active goals and `list_reminders`
   for what is due today or overdue. Lead with anything overdue.
3. **Headlines.** `news` with no topic (or the user's current focus from
   memory) — pick at most 3 headlines that matter, one line each.
4. **One suggestion.** Based on goals + reminders, propose the single most
   useful next action for today.
5. **Deliver.** Finish with the briefing in this shape:

   **☀️ Briefing — <date>**
   - Weather: …
   - Due today: …
   - Active goals: …
   - Headlines: …
   - Suggested next action: …

   Optionally `save_artifact` the briefing as markdown titled
   "Briefing — <date>".
