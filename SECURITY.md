# Security Policy

## Reporting

Use GitHub private vulnerability reporting if it is enabled for this repository. If it is not enabled, open a minimal public issue that says a security report is available, but do not include private feed URLs, exploit details, or secrets.

## Scope

Security-sensitive reports include unsafe feed parsing, SSRF-like fetch behavior, private feed leakage, dependency compromise, and workflows that expose repository tokens.

## Handling

Maintainers should confirm receipt, reproduce the issue in a private branch when needed, rotate any exposed credentials, and publish a fix with a short advisory or release note.

