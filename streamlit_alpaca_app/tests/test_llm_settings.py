"""Settings persistence and transport checks; these do not judge generated prose."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from services import llm, pipeline_store
from api import main as api
from services.api_auth import AuthPrincipal


class Store:
    def __init__(self):
        self.rows = {'shared_prompt': 'legacy', 'production::shared_prompt': 'production'}
        self.sql = []
        self.fail = False

    def connect(self):
        owner = self
        class Connection:
            def __enter__(self):
                self.before = deepcopy(owner.rows)
                return self
            def __exit__(self, kind, *_):
                if kind: owner.rows = self.before
            def cursor(self): return self
            def close(self): pass
            def execute(self, sql, args):
                owner.sql.append((sql, args))
                if sql.startswith('INSERT'):
                    if owner.fail: raise RuntimeError('database unavailable')
                    owner.rows[args[0]] = args[1]
                else:
                    self.scope = args[1].removesuffix('%')
            def fetchall(self):
                return [(key, value) for key, value in owner.rows.items() if '::' not in key or key.startswith(self.scope)]
        return Connection()


@pytest.fixture
def store(monkeypatch):
    db = Store()
    monkeypatch.setenv('APP_TRACK', 'development')
    monkeypatch.setattr(pipeline_store, 'open_database_connection', db.connect)
    monkeypatch.setattr(llm, '_PROMPT_OVERRIDES', {})
    monkeypatch.setattr(llm, '_OVERRIDES_LOADED', False)
    monkeypatch.setattr(llm, '_OVERRIDES_SCOPE', '')
    monkeypatch.setattr(llm, '_OVERRIDES_CHECKED_AT', 0)
    return db


def test_patch_preserves_other_keys_and_environment_and_reset_masks_legacy(store):
    assert llm.load_prompt_overrides(force=True)
    assert llm._PROMPT_OVERRIDES['shared_prompt'] == 'legacy'
    assert llm.save_prompt_override_changes({'shared_prompt': 'dev', 'new_prompt': 'new'})
    assert store.rows['production::shared_prompt'] == 'production'
    assert store.rows['shared_prompt'] == 'legacy'
    assert llm.save_prompt_override_changes({'shared_prompt': None})
    assert 'shared_prompt' not in llm._PROMPT_OVERRIDES
    assert llm._PROMPT_OVERRIDES['new_prompt'] == 'new'
    assert all('DELETE' not in sql and 'CREATE' not in sql for sql, _ in store.sql)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('APP_TRACK', 'production')
        assert llm.load_prompt_overrides(force=True)
        assert llm._PROMPT_OVERRIDES['shared_prompt'] == 'production'
        assert 'new_prompt' not in llm._PROMPT_OVERRIDES


def test_failed_write_does_not_change_active_state(store):
    llm.load_prompt_overrides(force=True)
    before = deepcopy(llm._PROMPT_OVERRIDES)
    store.fail = True
    assert not llm.save_prompt_override_changes({'shared_prompt': 'unsaved'})
    assert llm._PROMPT_OVERRIDES == before


def test_failed_initial_load_can_retry(monkeypatch, store):
    with monkeypatch.context() as patch:
        patch.setattr(pipeline_store, 'open_database_connection', lambda: None)
        assert not llm.load_prompt_overrides(force=True)
    assert llm.load_prompt_overrides(force=True)
    assert llm._PROMPT_OVERRIDES['shared_prompt'] == 'legacy'


def test_language_policy_is_resolved_for_each_request_without_changing_machine_values(store):
    session = type('Session', (), {})()
    def post(url, **kwargs):
        session.payload = kwargs['json']
        return type('Response', (), {'status_code': 200, 'json': lambda self: {'choices': [{'message': {'content': '{"url":"https://example.com/robust","enum":"granular","quote":"robust leverage"}'}}]}})()
    session.post = post
    client = llm.DeepSeekChatJSONClient(llm.LLMConfig(provider='deepseek', api_key='test', model='deepseek-v4-pro', base_url='https://api.deepseek.com', reasoning_effort='max'), session=session)
    result = client.generate_json(system_prompt='Task', user_prompt='Input', schema_name='probe', schema={'type': 'object'})
    assert result == {'url': 'https://example.com/robust', 'enum': 'granular', 'quote': 'robust leverage'}
    assert session.payload['reasoning_effort'] == 'max'
    assert session.payload['thinking'] == {'type': 'enabled'}
    assert 'temperature' not in session.payload
    assert llm.save_prompt_override_changes({'__narrative_style_rule__': 'A newly saved rule.'})
    client.generate_json(system_prompt='Task', user_prompt='Input', schema_name='probe', schema={'type': 'object'})
    assert 'A newly saved rule.' in session.payload['messages'][0]['content']


def test_invalid_settings_are_rejected_before_write(store):
    key = llm.register_config_param('Settings contract integer', 'QA', 1, 'Integer')
    for changes in [{key: 1.5}, {key: float('nan')}, {'unknown': 'value'}, {'__narrative_style_rule__': ''}]:
        with pytest.raises(ValueError): llm.validate_llm_setting_changes(changes)
    assert not store.sql


def test_llm_mutation_requires_admin_session(monkeypatch):
    client = TestClient(api.app)
    monkeypatch.setattr(api.api_auth, 'principal_from_access_token', lambda token: AuthPrincipal(principal_type='agent', auth_source='qa', scopes=(), user_context=None))
    response = client.patch('/v1/admin/llm', headers={'Authorization': 'Bearer qa'}, json={'changes': {'__narrative_style_rule__': None}})
    assert response.status_code == 403


def test_runtime_patch_applies_to_new_clients_without_changing_provider_credentials(store, monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'deepseek')
    monkeypatch.setenv('LLM_API_KEY', 'test')
    monkeypatch.setenv('LLM_MODEL', 'deepseek-reasoner')
    monkeypatch.setenv('LLM_BASE_URL', 'https://api.deepseek.com')
    changes = llm.validate_llm_setting_changes({'runtime:deepseek:model':'deepseek-v4-pro','runtime:deepseek:thinking_mode':'enabled','runtime:deepseek:reasoning_effort':'max'})
    assert llm.save_prompt_override_changes(changes)
    client = llm.load_llm_client()
    assert client.config.model == 'deepseek-v4-pro'
    assert client.config.reasoning_effort == 'max'
    assert client.config.provider == 'deepseek'
    assert client.config.api_key == 'test'
    assert llm.save_prompt_override_changes({'runtime:deepseek:model':None})
    assert llm.load_llm_client().config.model == 'deepseek-reasoner'
