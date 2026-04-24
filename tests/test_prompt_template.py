import string
from core.llm.edu_carousel_pipeline import BASE_USER_PROMPT

def test_prompt_has_only_expected_placeholders():
    fmt = string.Formatter()
    fields = {fname for _, fname, _, _ in fmt.parse(BASE_USER_PROMPT) if fname}
    expected = {
        'preserve_terms_block', 'glossary_block', 'tone_guidance',
        'client_name', 'client_id', 'source_type', 'source_url',
        'source_content',
    }
    assert fields == expected, f"Extra={fields-expected}, Missing={expected-fields}"
