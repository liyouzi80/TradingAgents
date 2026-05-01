import os
from typing import Any, Optional

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from .base_client import BaseLLMClient, normalize_content
from .validators import validate_model

# ── DeepSeek V4 thinking mode compat ──────────────────────────────────────
# DeepSeek V4's thinking mode returns `reasoning_content` in assistant
# messages that involved tool calls.  langchain's ChatOpenAI drops this field
# during message serialisation, causing DeepSeek to respond with 400:
#   "The reasoning_content in the thinking mode must be passed back to the API."
#
# We monkey-patch two internal functions so `reasoning_content` is preserved
# across the round-trip: response → AIMessage → next API request.

import langchain_openai.chat_models.base as _lc_base

_original_convert_message_to_dict = _lc_base._convert_message_to_dict


def _patched_convert_message_to_dict(message, api="chat/completions"):
    result = _original_convert_message_to_dict(message, api)
    if isinstance(message, AIMessage):
        rc = message.additional_kwargs.get("reasoning_content")
        if rc is not None:
            result["reasoning_content"] = rc
    return result


_lc_base._convert_message_to_dict = _patched_convert_message_to_dict


class NormalizedChatOpenAI(ChatOpenAI):
    """ChatOpenAI with normalized content output and DeepSeek V4 thinking mode support.

    The Responses API returns content as a list of typed blocks
    (reasoning, text, etc.). This normalizes to string for consistent
    downstream handling.

    Additionally preserves ``reasoning_content`` from DeepSeek V4 responses
    so it can be passed back in subsequent requests.
    """

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))

    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info)
        response_dict = (
            response
            if isinstance(response, dict)
            else response.model_dump(
                exclude={"choices": {"__all__": {"message": {"parsed"}}}}
            )
        )
        for i, choice in enumerate(response_dict.get("choices", [])):
            rc = choice.get("message", {}).get("reasoning_content")
            if rc is not None and i < len(result.generations):
                result.generations[i].message.additional_kwargs["reasoning_content"] = rc
        return result

    def with_structured_output(self, schema, *, method=None, **kwargs):
        """Wrap with structured output, defaulting to function_calling for OpenAI.

        langchain-openai's Responses-API-parse path (the default for json_schema
        when use_responses_api=True) calls response.model_dump(...) on the OpenAI
        SDK's union-typed parsed response, which makes Pydantic emit ~20
        PydanticSerializationUnexpectedValue warnings per call. The function-calling
        path returns a plain tool-call shape that does not trigger that
        serialization, so it is the cleaner choice for our combination of
        use_responses_api=True + with_structured_output. Both paths use OpenAI's
        strict mode and produce the same typed Pydantic instance.
        """
        if method is None:
            method = "function_calling"
        return super().with_structured_output(schema, method=method, **kwargs)

# Kwargs forwarded from user config to ChatOpenAI
_PASSTHROUGH_KWARGS = (
    "timeout", "max_retries", "reasoning_effort",
    "api_key", "callbacks", "http_client", "http_async_client",
)

# Provider base URLs and API key env vars
_PROVIDER_CONFIG = {
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY"),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "qwen": ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
    "glm": ("https://api.z.ai/api/paas/v4/", "ZHIPU_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
}


# ── DeepSeek legacy model aliases ──────────────────────────────────────────
# deepseek-chat and deepseek-reasoner are deprecated (2026/07/24).
# They transparently route to deepseek-v4-flash with appropriate thinking mode.

_DEEPSEEK_MODEL_ALIASES = {
    "deepseek-chat": "deepseek-v4-flash",       # → non-thinking mode
    "deepseek-reasoner": "deepseek-v4-flash",   # → thinking mode (default)
}


class OpenAIClient(BaseLLMClient):
    """Client for OpenAI, Ollama, OpenRouter, and xAI providers.

    For native OpenAI models, uses the Responses API (/v1/responses) which
    supports reasoning_effort with function tools across all model families
    (GPT-4.1, GPT-5). Third-party compatible providers (xAI, OpenRouter,
    Ollama) use standard Chat Completions.
    """

    def __init__(
        self,
        model: str,
        base_url: Optional[str] = None,
        provider: str = "openai",
        **kwargs,
    ):
        super().__init__(model, base_url, **kwargs)
        self.provider = provider.lower()

    def get_llm(self) -> Any:
        """Return configured ChatOpenAI instance."""
        self.warn_if_unknown_model()

        # Resolve DeepSeek legacy model aliases transparently
        model_name = self.model
        extra_body = {}
        if self.provider == "deepseek":
            resolved = _DEEPSEEK_MODEL_ALIASES.get(model_name)
            if resolved:
                model_name = resolved
                if self.model == "deepseek-chat":
                    extra_body["thinking"] = {"type": "disabled"}
                # deepseek-reasoner → thinking enabled by default, no extra_body needed

        llm_kwargs = {"model": model_name}
        if extra_body:
            llm_kwargs["extra_body"] = extra_body

        # Provider-specific base URL and auth
        if self.provider in _PROVIDER_CONFIG:
            base_url, api_key_env = _PROVIDER_CONFIG[self.provider]
            llm_kwargs["base_url"] = base_url
            if api_key_env:
                api_key = os.environ.get(api_key_env)
                if api_key:
                    llm_kwargs["api_key"] = api_key
            else:
                llm_kwargs["api_key"] = "ollama"
        elif self.base_url:
            llm_kwargs["base_url"] = self.base_url

        # Forward user-provided kwargs
        for key in _PASSTHROUGH_KWARGS:
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        # Native OpenAI: use Responses API for consistent behavior across
        # all model families. Third-party providers use Chat Completions.
        if self.provider == "openai":
            llm_kwargs["use_responses_api"] = True

        return NormalizedChatOpenAI(**llm_kwargs)

    def validate_model(self) -> bool:
        """Validate model for the provider."""
        return validate_model(self.provider, self.model)
