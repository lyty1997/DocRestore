<!--
Copyright 2026 @lyty1997

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# PII Redaction Layer

## 1. Overview

The PII (Personally Identifiable Information) redaction layer sanitizes sensitive information before documents are sent to cloud LLMs, reducing privacy risks.

Location: `backend/docrestore/privacy/`

## 2. Module Structure

```
privacy/
├── patterns.py    # Structured PII regexes (phone/email/ID card/bank card)
└── redactor.py    # PIIRedactor + EntityLexicon
```

## 3. Core Interface

### 3.1 PIIRedactor

```python
class PIIRedactor:
    def __init__(self, config: PIIConfig) -> None: ...

    async def redact_for_cloud(
        self,
        text: str,
        refiner: LLMRefiner | None,
    ) -> tuple[str, list[RedactionRecord], EntityLexicon | None]:
        """Redact PII from the merged document for cloud LLM consumption.

        Flow:
        1. Structured regex redaction (phone/email/ID card/bank card)
        2. Custom sensitive words replaced in descending order by word length
           (each word uses its own code; falls back if code is empty)
        3. If refiner is provided, call refiner.detect_pii_entities()
           to detect person names / organization names
        4. Build EntityLexicon and replace entities

        Returns:
            (redacted text, redaction records list, entity lexicon | None)
        """

    def redact_snippet(
        self, text: str, lexicon: EntityLexicon | None,
    ) -> tuple[str, list[RedactionRecord]]:
        """Redact a short snippet (e.g. re-OCR text) reusing an existing
        entity lexicon, without calling the LLM again."""
```

### 3.2 EntityLexicon

```python
@dataclass(frozen=True)
class EntityLexicon:
    """Entity lexicon detected by LLM (immutable for cross-page reuse)."""
    person_names: tuple[str, ...]
    org_names: tuple[str, ...]
```

> When entity detection fails or under a local provider, `redact_for_cloud` returns `None` as the lexicon (callers must handle the None case).

## 4. Redaction Strategy

### 4.1 Structured PII (Regex)

- Phone number: `1[3-9]\d{9}`
- Email: standard email regex
- ID card: 18 digits (including check digit)
- Bank card: 13-19 digits + Luhn validation

Default replacement placeholders (all overridable via `PIIConfig`):
- Phone: `[phone]` (`phone_placeholder`)
- Email: `[email]` (`email_placeholder`)
- ID card: `[id_card]` (`id_card_placeholder`)
- Bank card: `[bank_card]` (`bank_card_placeholder`)

### 4.2 Entity Detection (LLM)

Optionally enabled in cloud mode only:
- Calls `CloudLLMRefiner.detect_pii_entities()` to detect person / organization names
- Returns JSON: `{"person_names": [...], "org_names": [...]}`
- Builds an EntityLexicon and replaces entities

Default replacement placeholders:
- Person name: `[person_name]` (`person_name_placeholder`)
- Organization name: `[org_name]` (`org_name_placeholder`)

## 5. Configuration

Both `CustomWord` and `PIIConfig` are pydantic `BaseModel` instances (all configuration is unified under pydantic).

```python
class CustomWord(BaseModel):
    """Custom sensitive word entry. When code is non-empty it is used as the
    replacement; otherwise falls back to custom_words_placeholder."""
    model_config = ConfigDict(frozen=True)  # hashable
    word: str
    code: str = ""

class PIIConfig(BaseModel):
    enable: bool = False                          # Whether to enable PII redaction
    block_cloud_on_detect_failure: bool = True    # Block cloud calls when entity detection fails
    custom_sensitive_words: list[CustomWord] = []
    custom_words_placeholder: str = "[redacted]"  # Default placeholder when no code is specified
    # Other fields: see data-models.md Section 4.8
```

The API layer's `CustomSensitiveWord` (`api/schemas.py`) is a pydantic request model that accepts `list[str] | list[{word, code?}]`; the route helper `_to_custom_words()` converts them uniformly into `CustomWord` instances for the `pii_override`.

### Custom Sensitive Words to Code Mapping

To alleviate the readability issues caused by the same placeholder appearing repeatedly, users can assign an independent code to each sensitive word:

- `CustomWord(word="John Doe", code="Alias-A")` -- occurrences of `John Doe` in the text are replaced with `Alias-A`.
- `CustomWord(word="Acme Corp")` (code left empty) -- falls back to the default placeholder `[redacted]`.
- Replacement order is still descending by `word` length, preventing shorter words from matching first (e.g. "John" before "John Doe").
- `RedactionRecord` aggregates counts by the actual placeholder used; multiple codes produce multiple records.

## 6. Failure Strategy

- Regex redaction failure: log a warning and continue
- Entity detection failure + `block_cloud_on_detect_failure=True`: skip all cloud LLM calls
- Entity detection failure + `block_cloud_on_detect_failure=False`: proceed with regex-only redaction results

## 7. Data Flow

```
MergedDocument (after merge)
    |
    v PIIRedactor.redact_for_cloud()
    |-- Regex redaction (phone/email/ID card/bank card)
    |-- LLM entity detection (optional: person/organization names)
    +-- Entity replacement
    |
    v (redacted text, RedactionRecord[], EntityLexicon)
    |
    -> Enters LLM refinement stage
```

## 8. Notes

- Filename: `patterns.py`, not `regex.py` (to avoid mypy module name conflicts)
- Bank card validation: uses the Luhn algorithm to reduce false positives
- Entity detection: only available in cloud mode (LocalLLMRefiner lacks this capability)
- Re-OCR redaction: text from re-OCR during gap filling also requires redaction

## 9. Related Documents

- [Data Models](data-models.md) - `RedactionRecord`, `PIIConfig`
- [LLM Layer](llm.md) - `CloudLLMRefiner.detect_pii_entities()`
- [Pipeline](pipeline.md) - Position of PII redaction in the data flow
