import pytest

from integrations.automation_llm import StructuredAutomationProposalGenerator


class Router:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate_structured_response(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_structured_generator_uses_router_json_output_and_minimal_core_context_only():
    router = Router({"kind": "proposal", "draft": {"triggerType": "phone_usage_observed"}})
    generator = StructuredAutomationProposalGenerator(router)

    result = generator.generate(
        "If I check my phone three times, log Phone Distraction.",
        capabilities={"version": "2026-09-07", "triggers": ["phone_usage_observed"]},
        eligible_habits=[{"id": "habit-1", "name": "Phone Distraction"}],
    )

    assert result["kind"] == "proposal"
    prompt = router.calls[0]["prompt"]
    assert "Phone Distraction" in prompt and "habit-1" in prompt
    assert "Authorization" not in prompt and "token" not in prompt.lower()


@pytest.mark.parametrize("response", [None, [], "not-json"])
def test_structured_generator_rejects_malformed_provider_output(response):
    generator = StructuredAutomationProposalGenerator(Router(response))

    with pytest.raises(ValueError, match="structured"):
        generator.generate("make an automation", capabilities={"version": "2026-09-07"}, eligible_habits=[])
