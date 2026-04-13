# Experiment Summary Clarity + ElevenLabs Key Vault

## Goal

- Make the Experiment page tape summary easier to scan and easier to listen to.
- Stop depending on local ElevenLabs secrets for normal runtime setup.
- Store the ElevenLabs API key in Azure Key Vault instead of repo or local env files.

## Approach

1. Rewrite the summary output into a short lead plus compact bullet sections.
2. Keep a separate plain-text audio script so TTS still sounds natural.
3. Default ElevenLabs secret resolution to Key Vault secret names:
   - `elevenlabs-api-key`
   - `elevenlabs-voice-id`
4. Store the supplied API key in `spectral-nature-kvault` under `elevenlabs-api-key`.
5. Autogenerate narration on render and rely on the cached synthesis key to avoid repeated paid requests for unchanged summary text.

## Operational Notes

- The supplied ElevenLabs API key was stored in Key Vault successfully.
- The same key does not have `voices_read`, so voice discovery through `/v1/voices` returns `401 missing_permissions`.
- `elevenlabs-voice-id` is still missing from `spectral-nature-kvault`, so live audio remains blocked until a voice ID is provided or stored separately.
