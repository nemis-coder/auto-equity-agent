import pytest
from pydantic import ValidationError

from app.config import Settings

BASE = {
    "database_url": "postgresql+psycopg://u:p@localhost/db",
    "s3_endpoint_url": "http://localhost:9000",
    "s3_access_key_id": "app",
    "s3_secret_access_key": "secret",
}


def test_evaluator_profile_is_valid_without_external_accounts():
    settings = Settings(**BASE, langfuse_enabled=False)
    assert settings.langfuse_enabled is False
    assert settings.extraction_provider == "fake"


def test_langfuse_enabled_without_keys_fails_with_explicit_message():
    with pytest.raises(ValidationError) as exc:
        Settings(**BASE, langfuse_enabled=True, langfuse_base_url="https://cloud.langfuse.com")
    msg = str(exc.value)
    assert "LANGFUSE_PUBLIC_KEY" in msg and "LANGFUSE_SECRET_KEY" in msg
    assert "LANGFUSE_ENABLED=false" in msg


def test_langfuse_enabled_with_keys_is_valid():
    settings = Settings(
        **BASE,
        langfuse_enabled=True,
        langfuse_base_url="https://cloud.langfuse.com",
        langfuse_public_key="pk-lf-x",
        langfuse_secret_key="sk-lf-x",
    )
    assert settings.langfuse_enabled



@pytest.mark.parametrize(("extraction", "key"), [("fake", "k"), ("anthropic", None)])
def test_real_ai_mode_never_accepts_fake_extraction_or_missing_key(extraction, key):
    with pytest.raises(ValidationError):
        Settings(**BASE, app_mode="real_ai", extraction_provider=extraction, anthropic_api_key=key)


@pytest.mark.parametrize("provider", ["gemini", "gateway"])
def test_removed_providers_fail_explicitly(provider):
    with pytest.raises(ValidationError):
        Settings(**BASE, extraction_provider=provider)


def test_real_provider_requires_anthropic_key():
    with pytest.raises(ValidationError, match="ANTHROPIC_API_KEY"):
        Settings(**BASE, extraction_provider="anthropic")


def test_openai_requires_its_own_key_and_an_explicit_model():
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(**BASE, extraction_provider="openai", anthropic_api_key="not-openai",
                 extraction_model="openai-test-snapshot")
    for model in (None, "", "  "):
        with pytest.raises(ValidationError, match="EXTRACTION_MODEL"):
            Settings(**BASE, extraction_provider="openai", openai_api_key="test",
                     extraction_model=model)
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(**BASE, extraction_provider="openai", openai_api_key="  ",
                 extraction_model="openai-test-snapshot")


def test_openai_factory_uses_responses_without_storage_or_hidden_retries():
    from app.api.main import build_extractor

    settings = Settings(**BASE, app_mode="real_ai", extraction_provider="openai",
                        openai_api_key="test", extraction_model="openai-test-snapshot")
    extractor = build_extractor(settings)
    assert (extractor.name, extractor.model) == ("openai", "openai-test-snapshot")
    assert type(extractor._chat).__name__ == "ChatOpenAI"
    assert extractor._chat.max_retries == 0
    assert extractor._chat.request_timeout == 60.0
    assert extractor._chat.use_responses_api is True
    assert extractor._chat.store is False
    assert extractor._chat.use_previous_response_id is False
    assert settings.missing_llm_key("openai") is None
    assert "test" not in str(settings.openai_api_key)


def test_extractor_factory_keeps_evaluated_model_and_no_hidden_retries():
    from app.api.main import build_extractor

    settings = Settings(**BASE, extraction_provider="anthropic", anthropic_api_key="a")
    extractor = build_extractor(settings)
    assert (extractor.name, extractor._model) == ("anthropic", "claude-opus-5")
    assert type(extractor._chat).__name__ == "ChatAnthropic"
    assert extractor._chat.max_retries == 0
    assert extractor._chat.default_request_timeout == 60.0

    custom = settings.model_copy(update={"extraction_model": "another-snapshot"})
    assert build_extractor(custom)._model == "another-snapshot"


def test_suite_is_hermetic_even_with_real_keys_in_env():
    settings = Settings(**BASE)
    assert settings.extraction_provider == "fake"
    assert settings.langfuse_enabled is False and settings.langfuse_public_key is None
    assert settings.anthropic_api_key is None
    assert settings.openai_api_key is None
