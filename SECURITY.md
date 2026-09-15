# Security

Retrace v0.2 is an early-alpha local development tool for trusted Python workflows.

- Workflow modules execute arbitrary Python in the worker's process. Do not import definitions
  from untrusted sources. Retrace is not a sandbox.
- Inputs, outputs, and exception messages are stored in plaintext SQLite. Do not persist secrets
  or regulated data without an appropriate storage/access design. No automatic redaction is provided.
- The inspector is read-only and binds to `127.0.0.1`. Host/Origin validation and a Content Security
  Policy reduce browser-based access risks; they do not authenticate other local processes/users.
  Do not expose it through a public proxy or tunnel. No remote hosting mode is supported.
- Use local filesystems. Restrict database and artifact permissions using your operating system.
  Retrace does not encrypt databases or manage encryption keys.
- Stable idempotency keys help downstream deduplication; they are identifiers, not secrets or
  authentication credentials. Fencing covers database writes, not effects in remote systems.

For a vulnerability, use GitHub's private vulnerability reporting if enabled for this repository.
Otherwise contact the maintainer through the contact method on their GitHub profile before posting
sensitive reproduction details. General reliability bugs can be filed as normal issues with a minimal,
sanitized workflow. The latest alpha release is the only supported version at present.
