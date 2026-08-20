---
name: clarify-plan
description: Convert planning ambiguities into one pre-generation confirmation packet with bounded deterministic options and ordinary-language evidence.
---

# Clarify plan

Invoke `clarify-plan` through `SkillRuntime` after deterministic planning. It emits `clarification-packet/v1`.

Classify questions as auto-resolved, confirmation, presentation, or system error. Provide two to
two to four bounded options for confirmation questions and identify one deterministic default. Always show the packet
once. Lock the plan revision after answers; do not interrupt generation with new questions.
