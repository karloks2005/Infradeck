from src.agent.providers import NoApiKey
from src.backend.main import agent_error_detail, agent_error_message


def test_overloaded_provider_error_is_explained_to_the_user():
    message = agent_error_message(Exception("503 RESOURCE_EXHAUSTED: model overloaded"))

    assert "overloaded" in message.lower()


def test_the_chat_gets_a_plain_sentence_and_only_admins_the_providers_words():
    error = Exception("Error code: 400 - {'type': 'invalid_request_error', 'message': 'Schemas contains too many parameters with union types'}")

    plain, detailed = agent_error_message(error), agent_error_message(error, detail=True)

    assert plain.startswith("The AI provider rejected the request") and "Provider error" not in plain and "Schemas" not in plain
    assert "Provider error" in detailed and "too many parameters" in detailed


def test_provider_error_redacts_api_keys():
    message = agent_error_message(Exception("status 429 api_key=AIzaSecretValue"), detail=True)

    assert "[redacted]" in message
    assert "AIzaSecretValue" not in message and "AIzaSecretValue" not in agent_error_detail(Exception("api_key=AIzaSecretValue"))


def test_openai_keys_are_redacted_too():
    message = agent_error_message(Exception("Error code: 401 - Incorrect API key provided: sk-proj-abc123SECRET"), detail=True)

    assert "sk-proj-abc123SECRET" not in message
    assert "rejected the API key" in message


def test_a_missing_key_says_where_to_set_it():
    assert "Admin › Incident agent" in agent_error_message(NoApiKey("The agent has no API key."))


def test_an_unknown_model_points_at_the_model_setting():
    assert "Check the model in Admin" in agent_error_message(Exception("Error code: 404 - The model `gpt-nope` does not exist"))
