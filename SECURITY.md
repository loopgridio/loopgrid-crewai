# Security

Do not record raw prompts, tool arguments, tool results, customer identifiers or credentials unless your application explicitly permits it. `capture_content=False` is the default; however policy, authority, context and other application-created records may contain sensitive data unless sanitized before calling LoopGrid. `capture_content=True` exports raw model/tool content. The listener never authorizes real actions. Run authentic human approval/authorization checks in the application and CrewAI hooks.

Report vulnerabilities privately through the LoopGrid GitHub security advisory flow after repository publication.


RC4 notes: exception messages from SDK/client transport are intentionally excluded from `assert_healthy()` diagnostics to avoid exposing sensitive URLs or headers. Only error stage, error class and decision reference are retained in a 256-entry bounded diagnostic history. Do not treat plain SHA-256 commitments as encryption for low-entropy secrets.
