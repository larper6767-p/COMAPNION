"""Phase 4: Hybrid fact extraction with structured outputs and validation.

This module improves the fact extraction pipeline with:
- Structured JSON schema for model outputs
- Validation and fallback patterns
- Semantic deduplication using embeddings
- Durability classification
- Measured extraction quality

The original memory_hygiene.py is preserved for backward compatibility.
This module wraps and extends it with Phase 4 improvements.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.memory_hygiene import clean_fact, is_durable_fact, parse_facts

logger = logging.getLogger(__name__)


# ── Schema and validation ─────────────────────────────────────────────────────

@dataclass
class FactExtraction:
    """Structured result from fact extraction."""
    facts: List[str] = field(default_factory=list)
    rejected: List[Tuple[str, str]] = field(default_factory=list)  # (fact, reason)
    method: str = "structured"  # structured, regex_fallback, or failed
    raw_output: str = ""
    
    @property
    def success(self) -> bool:
        """True if at least one fact was extracted."""
        return len(self.facts) > 0
    
    @property
    def total_candidates(self) -> int:
        """Total number of candidates extracted (accepted + rejected)."""
        return len(self.facts) + len(self.rejected)


def _extract_json_from_markdown(text: str) -> Optional[str]:
    """Extract JSON from markdown code blocks."""
    # Try ```json block first
    match = re.search(r'```json\s*\n(.*?)\n```', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    
    # Try any ``` block
    match = re.search(r'```\s*\n(.*?)\n```', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    
    # Try bare JSON
    if text.strip().startswith(('[', '{')):
        return text.strip()
    
    return None


def validate_extraction_schema(data: Any) -> Tuple[bool, List[str]]:
    """Validate extracted data matches expected schema.
    
    Expected schema:
    - List of strings: ["fact1", "fact2"]
    - Object with facts key: {"facts": ["fact1", "fact2"]}
    
    Returns (is_valid, facts_list)
    """
    if isinstance(data, list):
        if all(isinstance(item, str) for item in data):
            return True, [str(item) for item in data]
    
    if isinstance(data, dict):
        if "facts" in data and isinstance(data["facts"], list):
            if all(isinstance(item, str) for item in data["facts"]):
                return True, [str(item) for item in data["facts"]]
    
    return False, []


def extract_facts_structured(
    raw_output: str,
    existing_facts: Optional[List[str]] = None
) -> FactExtraction:
    """Extract and validate facts using structured JSON parsing.
    
    This is the Phase 4 structured extraction path with validation.
    Falls back to regex patterns if JSON parsing fails.
    """
    result = FactExtraction(raw_output=raw_output)
    existing_facts = existing_facts or []
    existing_keys = {clean_fact(f).casefold() for f in existing_facts if f}
    
    # Try structured JSON extraction
    json_text = _extract_json_from_markdown(raw_output)
    if json_text:
        try:
            data = json.loads(json_text)
            valid, candidates = validate_extraction_schema(data)
            
            if valid:
                result.method = "structured"
                for candidate in candidates:
                    fact = clean_fact(candidate)
                    if not fact:
                        result.rejected.append((candidate, "empty_after_cleaning"))
                        continue
                    
                    if not is_durable_fact(fact):
                        result.rejected.append((fact, "not_durable"))
                        continue
                    
                    key = fact.casefold()
                    if key in existing_keys:
                        result.rejected.append((fact, "duplicate"))
                        continue
                    
                    result.facts.append(fact)
                    existing_keys.add(key)
                
                return result
        except json.JSONDecodeError as e:
            logger.debug(f"[fact-extraction] JSON parse failed: {e}")
    
    # Fallback to regex patterns
    result.method = "regex_fallback"
    facts = parse_facts(raw_output)
    
    for fact in facts:
        key = fact.casefold()
        if key in existing_keys:
            result.rejected.append((fact, "duplicate"))
            continue
        result.facts.append(fact)
        existing_keys.add(key)
    
    if not result.facts and not result.rejected:
        result.method = "failed"
    
    return result


# ── Semantic deduplication ───────────────────────────────────────────────────

def semantic_dedupe(
    new_facts: List[str],
    existing_facts: List[str],
    vector_store: Optional[Any] = None,
    threshold: float = 0.92
) -> Tuple[List[str], List[Tuple[str, str, float]]]:
    """Deduplicate facts using semantic similarity.
    
    Args:
        new_facts: New facts to check
        existing_facts: Existing facts in memory
        vector_store: Optional vector store with similarity search
        threshold: Similarity threshold (0.92 = very similar)
    
    Returns:
        (unique_facts, duplicates) where duplicates is list of (new_fact, similar_to, score)
    """
    if not vector_store or not hasattr(vector_store, 'enabled') or not vector_store.enabled:
        # No vector store, return all facts as unique
        return new_facts, []
    
    unique = []
    duplicates = []
    
    for fact in new_facts:
        try:
            # Query for similar facts
            results = vector_store.query(fact, k=3)
            
            # Check if any result is too similar
            found_duplicate = False
            for text, score in results:
                if score >= threshold:
                    # Too similar to existing fact
                    duplicates.append((fact, text, score))
                    found_duplicate = True
                    logger.info(
                        f"[semantic-dedupe] rejected: {fact!r} "
                        f"(similar to: {text!r}, score={score:.3f})"
                    )
                    break
            
            if not found_duplicate:
                unique.append(fact)
        
        except Exception as e:
            logger.debug(f"[semantic-dedupe] query failed for {fact!r}: {e}")
            # On error, keep the fact
            unique.append(fact)
    
    return unique, duplicates


# ── Durability classifier ─────────────────────────────────────────────────────

def classify_durability(fact: str) -> Tuple[str, float]:
    """Classify fact durability using pattern matching.
    
    Returns (category, confidence) where category is:
    - "durable": Long-term fact (preferences, identity, routines)
    - "temporal": Time-bound fact (appointments, current events)
    - "ephemeral": Momentary state (current feelings, plans for today)
    
    Confidence is 0.0-1.0
    """
    fact_lower = fact.lower()
    
    # Ephemeral patterns
    ephemeral_patterns = [
        r'\b(right now|currently|today|tonight|this morning)\b',
        r'\b(at the moment|just now|i\'m feeling|my head is)\b',
        r'\b(i have (chicken|dinner|breakfast))\b',
        r'\b(i\'m (planning|thinking|gonna|going to))\b',
    ]
    
    for pattern in ephemeral_patterns:
        if re.search(pattern, fact_lower):
            return "ephemeral", 0.9
    
    # Temporal patterns
    temporal_patterns = [
        r'\b(tomorrow|yesterday|next week|last week)\b',
        r'\b(appointment|deadline|exam|interview)\b',
        r'\b(will (be|have|go))\b',
    ]
    
    for pattern in temporal_patterns:
        if re.search(pattern, fact_lower):
            return "temporal", 0.7
    
    # Durable patterns
    durable_patterns = [
        r'^the user (likes|loves|enjoys|prefers|hates|dislikes)',
        r'^the user (lives|works|studies|plays|draws|writes)',
        r'^the user\'s (favorite|name|birthday|occupation)',
        r'\b(always|usually|often|regularly|every (day|week|month))\b',
    ]
    
    for pattern in durable_patterns:
        if re.search(pattern, fact_lower):
            return "durable", 0.9
    
    # Default: assume durable if it passed is_durable_fact
    return "durable", 0.6


# ── Prompt templates ──────────────────────────────────────────────────────────

def build_extraction_prompt(user_text: str, reply_text: str, structured: bool = True) -> str:
    """Build the fact extraction prompt.
    
    Args:
        user_text: What the user said
        reply_text: What the assistant replied
        structured: If True, request JSON schema output
    
    Returns:
        The extraction prompt
    """
    if structured:
        return (
            "Extract durable facts the USER explicitly stated about themselves "
            "in this exchange.\n\n"
            "Rules:\n"
            "- Write each fact as a standalone third-person sentence starting with 'The user'\n"
            "- Keep only: preferences, routines, people, dates, places, ongoing projects\n"
            "- Exclude: greetings, questions, plans for today, temporary feelings, "
            "anything the assistant said, instructions about tools/internet/roleplay, "
            "anything you inferred\n\n"
            "Output ONLY valid JSON in this exact format:\n"
            '{"facts": ["The user likes X", "The user works as Y"]}\n'
            "or empty array if none:\n"
            '{"facts": []}\n\n'
            f"User: {user_text}\n"
            f"Assistant: {reply_text}"
        )
    else:
        # Fallback: less structured prompt
        return (
            "Extract durable facts the USER explicitly stated about themselves. "
            "Write each fact as 'The user ...'. "
            "Output ONLY a JSON list or [] if none.\n\n"
            f"User: {user_text}\nAssistant: {reply_text}"
        )


# ── Integration helpers ───────────────────────────────────────────────────────

def extract_and_validate(
    raw_output: str,
    existing_facts: List[str],
    vector_store: Optional[Any] = None,
    enable_semantic_dedupe: bool = True,
) -> FactExtraction:
    """Complete extraction pipeline with all Phase 4 improvements.
    
    This is the main entry point for Phase 4 fact extraction.
    """
    # Step 1: Structured extraction with validation
    result = extract_facts_structured(raw_output, existing_facts)
    
    if not result.facts:
        return result
    
    # Step 2: Semantic deduplication (if vector store available)
    if enable_semantic_dedupe and vector_store:
        unique, sem_dupes = semantic_dedupe(
            result.facts,
            existing_facts,
            vector_store
        )
        
        # Move semantic duplicates to rejected list
        for fact, similar_to, score in sem_dupes:
            result.rejected.append((fact, f"semantic_duplicate:{similar_to}:{score:.3f}"))
        
        result.facts = unique
    
    # Step 3: Classify durability for logging/metrics
    for fact in result.facts:
        category, confidence = classify_durability(fact)
        if category != "durable":
            logger.info(
                f"[durability] {fact!r} classified as {category} "
                f"(confidence={confidence:.2f})"
            )
    
    return result


__all__ = [
    "FactExtraction",
    "extract_facts_structured",
    "semantic_dedupe",
    "classify_durability",
    "build_extraction_prompt",
    "extract_and_validate",
]

