# Daily Tape Summary + Audio Plan

## Goal

- Add one top summary card on the Daily Tape page that rolls up the activity shown in the cards below it.
- Let the user generate an audio version of that summary text through ElevenLabs.

## Approach

1. Move the Daily Tape beat-building logic into a shared service so the app and omnibar use the same source.
2. Build one deterministic summary payload from:
   - `top_events`
   - `must_read_movers`
   - `unresolved_large_moves`
3. Render that payload in a new top card above the existing event and mover sections.
4. Add a small ElevenLabs service that:
   - loads the API key from env or Key Vault secret name
   - uses a configured voice id
   - turns the summary text into audio on demand
5. Cache generated audio by summary text and voice settings so reruns do not keep making paid requests.

## Reliability Notes

- The summary card should be built from the same retained surface summaries already used by the tape cards.
- Audio generation should be user-triggered, not automatic on every page load, to avoid surprise latency and spend.
- Voice choice should be configured, not hardcoded.

## Files Expected

- `streamlit_alpaca_app/services/attention_home_summary.py`
- `streamlit_alpaca_app/services/elevenlabs_tts.py`
- `streamlit_alpaca_app/app.py`
- `streamlit_alpaca_app/services/omnibar.py`
- `streamlit_alpaca_app/tests/test_services.py`
- `streamlit_alpaca_app/.env.example`
