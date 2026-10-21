"""Action-dependent JSON contracts only; not LLM financial-answer tests."""
import pytest
from jsonschema import Draft202012Validator
from services.zopedia_agent import _STEP_SCHEMA


def tool_request():
    return {"action":"tool_call", "reasoning":"", "tool_name":"example.tool",
        "tool_arguments":{}, "needs_more_tools":True,
        "public_update":{"summary":"", "finding":"", "next_step":""},
        "research_state":{k: ([] if k != "goal" else "") for k in
            ["goal","findings","answer_points","selected_source_ids","alternatives","open_questions","next_actions"]},
        "source_policy":"available", "knowledge_policy":"none", "request_review":False}


@pytest.mark.parametrize("confidence", [None, "none", "low", "medium", "high"])
def test_tool_request_does_not_require_an_answer_or_answer_confidence(confidence):
    payload=tool_request()
    Draft202012Validator(_STEP_SCHEMA).validate(payload)
    payload["confidence"]=confidence
    Draft202012Validator(_STEP_SCHEMA).validate(payload)


@pytest.mark.parametrize("confidence", [None, "none", ""])
def test_final_answer_keeps_explicit_confidence_contract(confidence):
    payload={**tool_request(), "action":"final", "answer_markdown":"Example", "confidence":confidence}
    assert list(Draft202012Validator(_STEP_SCHEMA).iter_errors(payload))
    payload.pop("confidence")
    assert list(Draft202012Validator(_STEP_SCHEMA).iter_errors(payload))


def test_valid_final_and_tool_argument_type_contracts_remain():
    payload={**tool_request(), "action":"final", "answer_markdown":"Example", "confidence":"low"}
    Draft202012Validator(_STEP_SCHEMA).validate(payload)
    payload=tool_request();payload["tool_arguments"]="{}"
    assert list(Draft202012Validator(_STEP_SCHEMA).iter_errors(payload))


def test_minimal_actions_and_partial_metadata_updates():
    validate=Draft202012Validator(_STEP_SCHEMA).validate
    validate({"action":"tool_call", "tool_name":"example.tool", "tool_arguments":{}})
    validate({"action":"final", "answer_markdown":"Example", "confidence":"low"})
    validate({"action":"tool_call", "tool_name":"example.tool", "tool_arguments":{},
        "research_state":{"findings":[]}, "public_update":{"next_step":""}})
    validate({"action":"tool_call", "tool_name":"example.tool", "tool_arguments":{},
        "confidence":"", "research_state":{"findings":None}, "public_update":None,
        "source_policy":None, "knowledge_policy":None, "request_review":None})


@pytest.mark.parametrize("payload", [
    {"action":"tool_call", "tool_name":"example.tool"},
    {"action":"tool_call", "tool_arguments":{}},
    {"action":"tool_call", "tool_name":"", "tool_arguments":{}},
    {"action":"tool_call", "tool_name":"example.tool", "tool_arguments":None},
    {"action":"final", "answer_markdown":"", "confidence":"low"},
])
def test_action_content_is_still_required(payload):
    assert list(Draft202012Validator(_STEP_SCHEMA).iter_errors(payload))


@pytest.mark.parametrize("invalid_optional_selection", [False, True])
def test_evidence_actions_execute_without_inactive_envelope_fields(tmp_path, monkeypatch, invalid_optional_selection):
    import time
    from types import SimpleNamespace
    from data_access import DataAccessLayer, QueryService
    from services import zopedia_agent as agent
    from services.llm_execution import LocalResearchExecution
    service=QueryService(DataAccessLayer()).with_supplied_evidence({"doc":{"content":{"value":2}}})
    read={"action":"read", "tool_name":"research.read_supplied_evidence",
          "tool_arguments":{"source_id":"doc"}, "answer":{"value":"unfinished"}}
    if invalid_optional_selection:
        read["keep_tool_call_ids"]=["unrecognized"]
    replies=iter([read, {"action":"answer", "answer":{"value":2}}])
    monkeypatch.setattr(agent,"generate_json_via_aql_zopedia_gateway",lambda **kwargs:next(replies))
    calls=[]
    execution=LocalResearchExecution(tmp_path,overrides={},budget_usd=0,
        input_usd_per_million=0,output_usd_per_million=0,cost_basis="local_resources_unpriced")
    with execution.activate():
        result=agent._run_evidence_model_step(llm_client=SimpleNamespace(),service=service,
            tool_calls=calls,context_state={},prompt=lambda visible:"Read the supplied JSON.",
            system_prompt="Return the requested object.",schema_name="mechanical_object",
            schema={"type":"object","properties":{"value":{"type":"integer"}},"required":["value"]},
            surface="mechanical",purpose="mechanical",deadline_monotonic=time.monotonic()+10,
            metadata={},progress_callback=None)
    assert result=={"value":2}
    assert len(calls)==1 and calls[0]["status"]=="completed"
