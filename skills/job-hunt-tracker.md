---
name: job-hunt-tracker
description: Turn a job description into a fit analysis, a tracking goal, and memory notes about the company
---

# Skill: Job Hunt Tracker

The user pastes or links a job description (JD). Turn it into a decision
and a tracked application:

1. **Get the JD.** If a URL is given, `fetch_page` it. If text is pasted,
   work from the text. Extract: role title, company, location, experience
   band, must-have skills, nice-to-haves, and any red flags (bond clauses,
   unpaid training, fees — flag these loudly, never recommend paying).
2. **Recall the user.** `recall_memory` for the user's real stack and
   experience. Compare honestly: strong matches, partial matches, and hard
   gaps. Never claim a skill the user does not have (e.g. Kafka, dbt,
   Azure Data Factory hands-on) — mark them as gaps or "upskilling".
3. **Score the fit.** Give a fit verdict (Strong / Partial / Weak) with
   the top 3 reasons each way, plus the likely screening questions this JD
   would produce.
4. **Record it.** `remember` one line: company, role, verdict, date.
   `create_goal` titled "Apply: <role> @ <company>" with a plan (tailor
   resume points, apply link, follow-up date). Save the full analysis as a
   markdown artifact titled "Fit analysis — <company> <role>".
5. **Finish** with the verdict, the single next action, and the goal id.
