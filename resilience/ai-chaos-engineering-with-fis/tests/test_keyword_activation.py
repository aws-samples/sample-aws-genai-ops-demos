"""Property-based tests for keyword activation.

Tests Property 6: Keyword Activation
Validates: Requirements 4.1
"""

import re
import yaml
from pathlib import Path
import pytest
from hypothesis import given, strategies as st, assume, settings, HealthCheck
from typing import List, Set


def get_required_keywords() -> Set[str]:
    """Get the set of required keywords based on requirements."""
    return {
        # Core chaos engineering terms
        "chaos engineering", "chaos", "fault injection", "fault", "injection",
        "resilience testing", "resilience", "reliability",
        "failure testing", "disaster recovery testing",
        
        # AWS FIS specific terms  
        "FIS", "AWS FIS", "experiment",
        
        # Additional related terms that should trigger activation
        "chaos monkey", "chaos testing", "failure simulation",
        "system resilience", "infrastructure testing"
    }


@st.composite
def generate_message_with_keywords(draw):
    """Generate user messages containing chaos engineering keywords."""
    # Select 1-3 keywords to include
    available_keywords = list(get_required_keywords())
    selected_keywords = draw(st.lists(
        st.sampled_from(available_keywords),
        min_size=1, max_size=3,
        unique=True
    ))
    
    # Generate message text around the keywords
    prefix = draw(st.text(min_size=0, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd', 'Zs'))))
    suffix = draw(st.text(min_size=0, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd', 'Zs'))))
    
    # Construct message with keywords
    message_parts = [prefix.strip()] if prefix.strip() else []
    message_parts.extend(selected_keywords)
    if suffix.strip():
        message_parts.append(suffix.strip())
    
    message = " ".join(message_parts)
    
    return message, selected_keywords


@st.composite  
def generate_message_without_keywords(draw):
    """Generate user messages that should NOT trigger activation."""
    # Generate text that doesn't contain any chaos engineering keywords
    non_trigger_words = [
        "database", "application", "deployment", "monitoring", "logging",
        "security", "performance", "optimization", "configuration", "backup",
        "network", "storage", "compute", "analytics", "machine learning",
        "development", "testing", "integration", "continuous", "pipeline"
    ]
    
    selected_words = draw(st.lists(
        st.sampled_from(non_trigger_words),
        min_size=1, max_size=5
    ))
    
    message = " ".join(selected_words)
    
    # Ensure message doesn't accidentally contain trigger keywords
    trigger_keywords = get_required_keywords()
    message_lower = message.lower()
    
    for keyword in trigger_keywords:
        assume(keyword.lower() not in message_lower)
    
    return message


class TestKeywordActivation:
    """Property-based tests for keyword activation."""
    
    def _load_power_keywords(self) -> List[str]:
        """Load keywords from POWER.md frontmatter."""
        power_md_path = Path(__file__).parent.parent / "POWER.md"
        
        with open(power_md_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Extract frontmatter
        if content.startswith('---'):
            end_marker = content.find('---', 3)
            if end_marker != -1:
                frontmatter = content[3:end_marker]
                try:
                    metadata = yaml.safe_load(frontmatter)
                    return metadata.get('keywords', [])
                except yaml.YAMLError:
                    return []
        
        return []
    
    def test_power_md_contains_required_keywords(self):
        """Test that POWER.md frontmatter contains all required keywords."""
        power_keywords = self._load_power_keywords()
        required_keywords = get_required_keywords()
        
        # Convert to lowercase for case-insensitive comparison
        power_keywords_lower = {kw.lower() for kw in power_keywords}
        
        # Check that all required keywords are present
        missing_keywords = []
        for required_keyword in required_keywords:
            if required_keyword.lower() not in power_keywords_lower:
                missing_keywords.append(required_keyword)
        
        assert len(missing_keywords) == 0, f"Missing required keywords in POWER.md: {missing_keywords}"
    
    def test_power_md_frontmatter_structure(self):
        """Test that POWER.md has proper frontmatter structure for keyword activation."""
        power_md_path = Path(__file__).parent.parent / "POWER.md"
        
        with open(power_md_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Verify frontmatter exists
        assert content.startswith('---'), "POWER.md must start with YAML frontmatter"
        
        end_marker = content.find('---', 3)
        assert end_marker != -1, "POWER.md frontmatter must be properly closed"
        
        frontmatter = content[3:end_marker]
        
        # Parse frontmatter
        try:
            metadata = yaml.safe_load(frontmatter)
        except yaml.YAMLError as e:
            pytest.fail(f"Invalid YAML in POWER.md frontmatter: {e}")
        
        # Verify required fields for Kiro Power
        required_fields = ['name', 'displayName', 'description', 'keywords', 'author']
        for field in required_fields:
            assert field in metadata, f"Missing required field '{field}' in POWER.md frontmatter"
        
        # Verify keywords is a list
        assert isinstance(metadata['keywords'], list), "Keywords must be a list in POWER.md frontmatter"
        assert len(metadata['keywords']) > 0, "Keywords list cannot be empty"
    
    @given(
        message_data=generate_message_with_keywords()
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_keyword_activation_property(self, message_data):
        """
        **Feature: aws-chaos-engineering-kiro-power, Property 6: Keyword Activation**
        
        For any user message containing chaos engineering keywords, 
        the Kiro Power should activate automatically.
        
        **Validates: Requirements 4.1**
        """
        message, selected_keywords = message_data
        
        # Load configured keywords from POWER.md
        power_keywords = self._load_power_keywords()
        power_keywords_lower = {kw.lower() for kw in power_keywords}
        
        # Verify that the message contains at least one configured keyword
        message_lower = message.lower()
        found_keywords = []
        
        for keyword in selected_keywords:
            if keyword.lower() in power_keywords_lower:
                # Check if keyword appears in the message
                if keyword.lower() in message_lower:
                    found_keywords.append(keyword)
        
        # Property: If message contains configured keywords, power should activate
        # This is verified by ensuring the keywords are properly configured in POWER.md
        assert len(found_keywords) > 0, f"Message '{message}' should contain at least one configured keyword"
        
        # Verify each found keyword is actually in the power configuration
        for keyword in found_keywords:
            assert keyword.lower() in power_keywords_lower, f"Keyword '{keyword}' not found in power configuration"
    
    @given(
        message=generate_message_without_keywords()
    )
    @settings(max_examples=50)
    def test_non_keyword_messages_should_not_activate(self, message):
        """Test that messages without chaos engineering keywords should not activate the power."""
        # Load configured keywords from POWER.md
        power_keywords = self._load_power_keywords()
        power_keywords_lower = {kw.lower() for kw in power_keywords}
        
        message_lower = message.lower()
        
        # Verify message doesn't contain any configured keywords
        found_keywords = []
        for keyword in power_keywords_lower:
            if keyword in message_lower:
                found_keywords.append(keyword)
        
        # Property: Messages without keywords should not trigger activation
        assert len(found_keywords) == 0, f"Message '{message}' should not contain keywords but found: {found_keywords}"
    
    def test_keyword_coverage_completeness(self):
        """Test that keyword configuration covers all required activation scenarios."""
        power_keywords = self._load_power_keywords()
        power_keywords_lower = {kw.lower() for kw in power_keywords}
        
        # Test scenarios that should trigger activation
        test_scenarios = [
            "I want to do chaos engineering on my system",
            "Help me with fault injection testing", 
            "Create an AWS FIS experiment",
            "I need resilience testing for my application",
            "Set up failure testing for disaster recovery",
            "Configure chaos monkey for my infrastructure",
            "Design a fault injection simulator experiment"
        ]
        
        for scenario in test_scenarios:
            scenario_lower = scenario.lower()
            found_match = False
            
            for keyword in power_keywords_lower:
                if keyword in scenario_lower:
                    found_match = True
                    break
            
            assert found_match, f"Test scenario '{scenario}' should match at least one configured keyword"
    
    @given(
        keyword_variations=st.lists(
            st.sampled_from([
                "chaos engineering", "Chaos Engineering", "CHAOS ENGINEERING",
                "fault injection", "Fault Injection", "FAULT INJECTION", 
                "fis", "FIS", "Fis",
                "aws fis", "AWS FIS", "Aws Fis",
                "resilience", "Resilience", "RESILIENCE"
            ]),
            min_size=1, max_size=3,
            unique=True
        )
    )
    @settings(max_examples=50)
    def test_case_insensitive_keyword_matching(self, keyword_variations):
        """Test that keyword matching works regardless of case variations."""
        power_keywords = self._load_power_keywords()
        power_keywords_lower = {kw.lower() for kw in power_keywords}
        
        # Test that case variations of configured keywords would be detected
        for keyword_variant in keyword_variations:
            keyword_lower = keyword_variant.lower()
            
            # Check if this variant matches any configured keyword
            matches_configured = any(
                configured_keyword in keyword_lower or keyword_lower in configured_keyword
                for configured_keyword in power_keywords_lower
            )
            
            # If it's a known chaos engineering term, it should match configuration
            known_terms = {"chaos", "engineering", "fault", "injection", "fis", "resilience"}
            if any(term in keyword_lower for term in known_terms):
                assert matches_configured, f"Keyword variant '{keyword_variant}' should match power configuration"
    
    def test_keyword_documentation_consistency(self):
        """Test that documented keywords in POWER.md content match frontmatter."""
        power_md_path = Path(__file__).parent.parent / "POWER.md"
        
        with open(power_md_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Extract frontmatter keywords
        frontmatter_keywords = self._load_power_keywords()
        frontmatter_keywords_lower = {kw.lower() for kw in frontmatter_keywords}
        
        # Find the "Automatic Activation" section in the content
        activation_section_match = re.search(
            r'### Automatic Activation.*?(?=###|\Z)', 
            content, 
            re.DOTALL | re.IGNORECASE
        )
        
        if activation_section_match:
            activation_section = activation_section_match.group(0)
            
            # Check that key frontmatter keywords are mentioned in the documentation
            core_keywords = ["chaos engineering", "fault injection", "FIS", "resilience"]
            
            for keyword in core_keywords:
                if keyword.lower() in frontmatter_keywords_lower:
                    assert keyword.lower() in activation_section.lower(), \
                        f"Core keyword '{keyword}' should be documented in Automatic Activation section"