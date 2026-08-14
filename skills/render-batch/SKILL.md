---
name: render-batch
description: Execute bounded, checkpointed Creo render jobs while isolating failures and preserving the source CAD model set.
---

# Render batch

Use one worker by default, one disposable model copy per worker session, and a checkpoint every
20 tasks. Retry external failures up to three times. Continue unrelated steps when one image
fails; wait only descendants of a structural failure.
