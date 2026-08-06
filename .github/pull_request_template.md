## Summary

- What changed:
- Why:
- Related issue:

## Risk

- Persistence / migration impact:
- Installer / scheduled-task impact:
- Privacy / network impact:

## Validation

- [ ] `uv run --no-sync pytest`
- [ ] `uv run --no-sync ruff check src tests tools`
- [ ] No-model/no-microphone self-test, when relevant
- [ ] `uv build` and wheel verification, when relevant

List additional commands and results:

## UI evidence

Attach only synthetic or fully de-identified screenshots/GIFs. Never attach recordings,
transcripts, databases, configuration files or unsanitized logs.

## Checklist

- [ ] Tests cover the changed behavior.
- [ ] User-visible changes are documented and added to `CHANGELOG.md`.
- [ ] Dependency or model changes update `THIRD_PARTY_NOTICES.md`.
- [ ] No runtime data, model weights, personal fonts, credentials or machine-local paths are committed.
