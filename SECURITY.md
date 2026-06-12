# Security Policy

Unshuffle performs local filesystem scanning, copying, moving, undo, and delete operations, so path-safety issues are treated seriously.

## Reporting A Vulnerability

Please do not open a public issue for vulnerabilities involving data loss, path traversal, unsafe deletion, unsafe overwrite behavior, or private data exposure.

Use GitHub private vulnerability reporting for this repository when it is enabled. If private vulnerability reporting is not available yet, contact the repository owner privately before sharing exploit details.

## Scope

Relevant reports include:

- Path traversal or destination-containment bypasses.
- Unsafe move/copy/delete behavior.
- Undo behavior that can affect files outside the intended library/session.
- Accidental exposure of local file paths, databases, or private sample metadata.
- Native extractor crashes triggered by malformed audio files.

## Response

Security response times are not yet formalized, but reproducible reports with clear steps and minimal test files will be prioritized.
