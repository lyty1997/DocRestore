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

    def redact_regex_only(
        self, text: str,
    ) -> tuple[str, list[RedactionRecord]]:
        """Structured regex (phone/email/ID card/bank card) + custom
        sensitive words only. No LLM, no EntityLexicon dependency. Used by
        the streaming Pipeline OCR Producer per page (entity detection has
        not happened yet, so no lexicon is available)."""

    def redact_tokens_only(
        self, text: str,
    ) -> tuple[str, list[RedactionRecord]]:
        """Code-body profile: only high-confidence credential tokens +
        custom sensitive words, with zero damage to normal code. Skips the
        full KV / phone / email / card / host / url regexes (which would
        corrupt ``password = get_secret()``) and does no entity replacement
        (protects import paths / identifiers). Idempotent."""

    def redact_snippet(
        self, text: str, lexicon: EntityLexicon | None,
    ) -> tuple[str, list[RedactionRecord]]:
        """Light redaction (regex + reuse of an existing lexicon), used for
        re-OCR text. Does not call the LLM."""
```

> The unified cloud gateway is `PIIGuard.redact_for_cloud(text, lexicon, *, profile)`
> (sync), which dispatches to the `PIIRedactor` methods above by profile. It is **not**
> the same as the deleted `PIIRedactor.redact_for_cloud(text, refiner)` (async, refiner
> argument), which was removed in S4 (2026-06-15). All cloud call sites route through
> the `PIIGuard` gateway. Entity detection now happens locally via
> `PIIGuard.detect_entities(text)` (local NER), so names never leave the machine.

### 3.2 EntityLexicon

```python
@dataclass(frozen=True)
class EntityLexicon:
    """Entity lexicon detected by LLM (immutable for cross-page reuse)."""
    person_names: tuple[str, ...]
    org_names: tuple[str, ...]
```

> When entity detection is not requested, local NER is disabled, or detection fails, `PIIGuard.detect_entities` returns `None` instead of a lexicon (callers must handle the None case and fail-closed per `block_cloud_on_detect_failure`).

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

### 4.2 Entity Detection (local NER)

> **Entity detection runs entirely on local NER** (`PIIGuard.detect_entities`, spaCy). The former
> cloud path `CloudLLMRefiner.detect_pii_entities` was removed in S4 (2026-06-15); names never leave
> the machine. See the design doc `pii-local-ner.md` (Chinese).

Current local path:
- Calls `PIIGuard.detect_entities(text)` to detect person / organization names via local NER
  (`privacy/ner.py::SpacyEntityDetector`)
- Builds an EntityLexicon and replaces entities
- Returns `None` (no lexicon) when entity redaction is not requested, NER is disabled, or detection
  fails -- callers fail-closed per `block_cloud_on_detect_failure`

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
    v PIIGuard.redact_for_cloud(text, lexicon, *, profile)
    |-- Regex redaction (phone/email/ID card/bank card)
    |-- Local NER entity detection (optional: person/organization names)
    +-- Entity replacement
    |
    v (redacted text, RedactionRecord[], EntityLexicon)
    |
    -> Enters LLM refinement stage
```

## 8. Notes

- Filename: `patterns.py`, not `regex.py` (to avoid mypy module name conflicts)
- Bank card validation: uses the Luhn algorithm to reduce false positives
- Entity detection: runs on local NER (`PIIGuard.detect_entities`), independent of the LLM provider; names never leave the machine
- Re-OCR redaction: text from re-OCR during gap filling also requires redaction

## 9. Related Documents

- [Data Models](data-models.md) - `RedactionRecord`, `PIIConfig`
- [LLM Layer](llm.md) - cloud `CloudLLMRefiner.detect_pii_entities()` was removed in S4 (2026-06-15); entity detection now uses local NER (`PIIGuard.detect_entities`)
- [Pipeline](pipeline.md) - Position of PII redaction in the data flow
