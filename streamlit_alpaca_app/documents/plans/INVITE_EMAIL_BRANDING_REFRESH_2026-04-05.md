# Invite Email Branding Refresh (2026-04-05)

## Goal

Upgrade account invite emails from plain text to a professional branded format with:

- Spectral Nature logo
- A portfolio-intelligence graph visual
- A clear activation CTA
- Plain-text fallback for clients that do not render HTML

## Design

1. Keep delivery source-of-truth in `services/auth_service.py`.
2. Keep SMTP transport concerns in `services/emailer.py`.
3. Use CID inline image attachments (multipart email) so logo/graph render without requiring external image hosting.
4. Keep a text fallback body for accessibility and client compatibility.

## Implementation

- Added `EmailInlineImage` in `services/emailer.py`.
- Extended `send_email()` to accept `inline_images` and attach them as inline related parts when HTML body is present.
- Added invite template helpers in `services/auth_service.py`:
  - `_invite_email_text(...)`
  - `_invite_email_html(...)`
  - `_load_inline_email_image(...)`
- Updated `issue_invite(...)` to send:
  - professional HTML body
  - plain text fallback
  - inline logo and graph images
- Added branded graph asset:
  - `branding/email/invite-performance-graph.png`

## Reliability Notes

- CID attachments are generally more consistent for email-client rendering than base64 `data:` URIs.
- If branding assets are unavailable, the send path still succeeds with text + HTML fallback content.

## Validation

- Added test coverage:
  - `tests/test_emailer.py` verifies inline image MIME attachment behavior.
  - `tests/test_auth_service.py` verifies branded invite HTML/text template content.
- Local run: targeted tests passed.
