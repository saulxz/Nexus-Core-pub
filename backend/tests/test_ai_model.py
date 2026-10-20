from app.ai.agent import _normalize_gemini_model


def test_gemini_model_name_is_normalized_for_the_api():
    assert _normalize_gemini_model("gemini 2.5 flash") == "gemini-2.5-flash"
    assert _normalize_gemini_model("models/gemini-2.5-flash") == "gemini-2.5-flash"
    assert _normalize_gemini_model("  GEMINI_2.5_FLASH  ") == "gemini-2.5-flash"
    assert _normalize_gemini_model("gemini-2.5-flash") == "gemini-2.5-flash"


def test_gemini_model_name_falls_back_when_empty():
    assert _normalize_gemini_model("") == "gemini-2.5-flash"
