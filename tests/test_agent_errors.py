from src.backend.main import agent_error_message


def test_overloaded_provider_error_is_explained_to_the_user():
    message = agent_error_message(Exception("503 RESOURCE_EXHAUSTED: model overloaded"))

    assert "overloaded" in message.lower()
    assert "Provider error" in message


def test_provider_error_redacts_api_keys():
    message = agent_error_message(Exception("status 429 api_key=AIzaSecretValue"))

    assert "[redacted]" in message
    assert "AIzaSecretValue" not in message
