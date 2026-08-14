---
name: intake-preflight
description: Validate the two user inputs and local Creo, Excel, J-Link, storage, and DashScope configuration before a Qwen Creo SOP run starts.
---

# Intake preflight

Accept only `run_id` and registered input artifact references. Verify that the BOM is readable,
the CAD directory is read-only safe, source hashes can be recorded, and required local services
are configured. Return stable error codes. Never create user delivery files or upload CAD data.
