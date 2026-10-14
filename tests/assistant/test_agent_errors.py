from src.backend.main import agent_error_message


def test_overloaded_provider_error_is_explained_to_the_user():
    message = agent_error_message(Exception("503 RESOURCE_EXHAUSTED: model overloaded"))

    assert "overloaded" in message.lower()
    assert "Provider error" in message


def test_provider_error_redacts_api_keys():
    message = agent_error_message(Exception("status 429 api_key=AIzaSecretValue"))

    assert "[redacted]" in message
    assert "AIzaSecretValue" not in message


def test_openai_keys_are_redacted_too():
    message = agent_error_message(Exception("Error code: 401 - Incorrect API key provided: sk-proj-abc123SECRET"))

    assert "sk-proj-abc123SECRET" not in message
    assert "rejected the API key" in message


def test_a_missing_key_says_where_to_set_it():
    assert "OPENAI_API_KEY" in agent_error_message(RuntimeError("OPENAI_API_KEY is not configured."))


def test_an_unknown_model_points_at_agent_model():
    assert "AGENT_MODEL" in agent_error_message(Exception("Error code: 404 - The model `gpt-nope` does not exist"))
