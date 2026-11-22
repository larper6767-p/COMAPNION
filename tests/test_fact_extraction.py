"""Phase 4: Tests for hybrid fact extraction with structured outputs."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.fact_extraction import (
    FactExtraction,
    extract_facts_structured,
    semantic_dedupe,
    classify_durability,
    build_extraction_prompt,
    extract_and_validate,
    validate_extraction_schema,
)


class TestFactExtraction:
    """Test the FactExtraction dataclass."""
    
    def test_default_values(self):
        result = FactExtraction()
        assert result.facts == []
        assert result.rejected == []
        assert result.method == "structured"
        assert result.raw_output == ""
    
    def test_success_property(self):
        result = FactExtraction(facts=["The user likes pizza"])
        assert result.success
        
        result = FactExtraction(facts=[])
        assert not result.success
    
    def test_total_candidates(self):
        result = FactExtraction(
            facts=["The user likes pizza"],
            rejected=[("I'm hungry", "ephemeral")]
        )
        assert result.total_candidates == 2


class TestSchemaValidation:
    """Test JSON schema validation."""
    
    def test_valid_list_schema(self):
        data = ["The user likes pizza", "The user lives in NYC"]
        valid, facts = validate_extraction_schema(data)
        assert valid
        assert facts == data
    
    def test_valid_object_schema(self):
        data = {"facts": ["The user likes pizza"]}
        valid, facts = validate_extraction_schema(data)
        assert valid
        assert facts == ["The user likes pizza"]
    
    def test_invalid_list_with_non_strings(self):
        data = ["fact", 123, None]
        valid, facts = validate_extraction_schema(data)
        assert not valid
        assert facts == []
    
    def test_invalid_object_wrong_key(self):
        data = {"items": ["fact"]}
        valid, facts = validate_extraction_schema(data)
        assert not valid
    
    def test_invalid_types(self):
        for data in [None, "string", 123, True]:
            valid, facts = validate_extraction_schema(data)
            assert not valid
            assert facts == []


class TestStructuredExtraction:
    """Test structured JSON extraction."""
    
    def test_extract_from_json_code_block(self):
        raw = '''```json
{"facts": ["The user likes pizza", "The user lives in NYC"]}
```'''
        result = extract_facts_structured(raw)
        assert result.method == "structured"
        assert len(result.facts) == 2
        assert "The user likes pizza" in result.facts
    
    def test_extract_from_plain_json_list(self):
        raw = '["The user draws", "The user plays games"]'
        result = extract_facts_structured(raw)
        assert result.method == "structured"
        assert len(result.facts) == 2
    
    def test_extract_with_existing_facts_dedupes(self):
        raw = '["The user likes pizza", "The user draws"]'
        existing = ["The user likes pizza"]
        result = extract_facts_structured(raw, existing)
        assert len(result.facts) == 1
        assert result.facts[0] == "The user draws"
        assert len(result.rejected) == 1
        assert result.rejected[0][1] == "duplicate"
    
    def test_rejects_ephemeral_facts(self):
        raw = '["The user likes pizza", "I have chicken right now"]'
        result = extract_facts_structured(raw)
        assert len(result.facts) == 1
        assert result.facts[0] == "The user likes pizza"
        assert len(result.rejected) == 1
        assert result.rejected[0][1] == "not_durable"
    
    def test_fallback_to_regex_on_invalid_json(self):
        raw = "invalid\n- The user likes pizza"
        result = extract_facts_structured(raw)
        assert result.method == "regex_fallback"
        assert "The user likes pizza" in result.facts
    
    def test_failed_method_when_no_facts_found(self):
        raw = "Nothing useful here"
        result = extract_facts_structured(raw)
        assert result.method == "failed"
        assert len(result.facts) == 0


class TestSemanticDedupe:
    """Test semantic deduplication with vector store."""
    
    def test_no_vector_store_returns_all_facts(self):
        new_facts = ["The user likes pizza", "The user plays games"]
        existing = []
        unique, dupes = semantic_dedupe(new_facts, existing, None)
        assert unique == new_facts
        assert dupes == []
    
    def test_disabled_vector_store_returns_all_facts(self):
        mock_store = MagicMock()
        mock_store.enabled = False
        new_facts = ["The user likes pizza"]
        unique, dupes = semantic_dedupe(new_facts, [], mock_store)
        assert unique == new_facts
        assert dupes == []
    
    def test_finds_semantic_duplicates(self):
        mock_store = MagicMock()
        mock_store.enabled = True
        mock_store.query.return_value = [
            ("The user enjoys pizza", 0.95),  # Very similar
            ("The user likes pasta", 0.70),   # Somewhat similar
        ]
        
        new_facts = ["The user likes pizza"]
        existing = ["The user enjoys pizza"]
        
        unique, dupes = semantic_dedupe(new_facts, existing, mock_store, threshold=0.92)
        
        assert len(unique) == 0
        assert len(dupes) == 1
        assert dupes[0][0] == "The user likes pizza"
        assert dupes[0][1] == "The user enjoys pizza"
        assert dupes[0][2] >= 0.92
    
    def test_keeps_distinct_facts(self):
        mock_store = MagicMock()
        mock_store.enabled = True
        mock_store.query.return_value = [
            ("The user lives in NYC", 0.30),  # Not similar
        ]
        
        new_facts = ["The user likes pizza"]
        unique, dupes = semantic_dedupe(new_facts, [], mock_store, threshold=0.92)
        
        assert len(unique) == 1
        assert len(dupes) == 0
    
    def test_handles_query_errors_gracefully(self):
        mock_store = MagicMock()
        mock_store.enabled = True
        mock_store.query.side_effect = Exception("Query failed")
        
        new_facts = ["The user likes pizza"]
        unique, dupes = semantic_dedupe(new_facts, [], mock_store)
        
        # On error, should keep the fact
        assert unique == new_facts
        assert dupes == []


class TestDurabilityClassifier:
    """Test fact durability classification."""
    
    def test_classifies_durable_facts(self):
        facts = [
            "The user likes pizza",
            "The user lives in NYC",
            "The user works as a developer",
            "The user's favorite band is MCR",
        ]
        for fact in facts:
            category, confidence = classify_durability(fact)
            assert category == "durable"
            assert confidence > 0.5
    
    def test_classifies_ephemeral_facts(self):
        facts = [
            "The user is feeling tired right now",
            "The user has chicken for dinner currently",
            "The user is planning something today",
        ]
        for fact in facts:
            category, confidence = classify_durability(fact)
            assert category == "ephemeral"
            assert confidence > 0.8
    
    def test_classifies_temporal_facts(self):
        facts = [
            "The user has an appointment tomorrow",
            "The user will go to the store next week",
            "The user had an exam yesterday",
        ]
        for fact in facts:
            category, confidence = classify_durability(fact)
            assert category == "temporal"
            assert confidence > 0.6
    
    def test_default_classification_for_ambiguous(self):
        fact = "The user mentioned something"
        category, confidence = classify_durability(fact)
        assert category == "durable"
        assert confidence < 0.9  # Lower confidence for ambiguous


class TestPromptBuilding:
    """Test extraction prompt generation."""
    
    def test_structured_prompt_format(self):
        prompt = build_extraction_prompt("I like pizza", "That's great!", structured=True)
        assert "JSON" in prompt
        assert '{"facts":' in prompt
        assert "User: I like pizza" in prompt
        assert "Assistant: That's great!" in prompt
    
    def test_unstructured_prompt_format(self):
        prompt = build_extraction_prompt("I like pizza", "That's great!", structured=False)
        assert "JSON" in prompt
        assert "User: I like pizza" in prompt


class TestEndToEndPipeline:
    """Test the complete extraction pipeline."""
    
    def test_extract_and_validate_structured_path(self):
        raw = '{"facts": ["The user likes pizza"]}'
        existing = []
        result = extract_and_validate(raw, existing, None, False)
        
        assert result.success
        assert len(result.facts) == 1
        assert result.method == "structured"
    
    def test_extract_and_validate_with_deduplication(self):
        raw = '{"facts": ["The user likes pizza"]}'
        existing = ["The user likes pizza"]
        result = extract_and_validate(raw, existing, None, False)
        
        assert not result.success
        assert len(result.facts) == 0
        assert len(result.rejected) == 1
    
    def test_extract_and_validate_with_semantic_dedupe(self):
        mock_store = MagicMock()
        mock_store.enabled = True
        mock_store.query.return_value = [
            ("The user enjoys pizza", 0.95),
        ]
        
        raw = '{"facts": ["The user likes pizza"]}'
        existing = []
        result = extract_and_validate(raw, existing, mock_store, True)
        
        # Should be rejected by semantic dedupe
        assert len(result.facts) == 0
        assert any("semantic_duplicate" in reason for _, reason in result.rejected)
    
    def test_extract_and_validate_fallback_path(self):
        raw = "- The user likes pizza"
        result = extract_and_validate(raw, [], None, False)
        
        assert result.success
        assert result.method == "regex_fallback"


class TestIntegrationWithMemoryHygiene:
    """Test integration with existing memory_hygiene module."""
    
    def test_uses_clean_fact_from_memory_hygiene(self):
        raw = '{"facts": ["- the user likes pizza."]}'
        result = extract_facts_structured(raw)
        # Should be cleaned: no bullet, no period, capitalized
        assert result.facts[0] == "The user likes pizza"
    
    def test_uses_is_durable_fact_from_memory_hygiene(self):
        raw = '{"facts": ["I have chicken right now"]}'
        result = extract_facts_structured(raw)
        # Should be rejected by is_durable_fact
        assert len(result.facts) == 0
        assert len(result.rejected) == 1

