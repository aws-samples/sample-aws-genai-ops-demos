"""
Property-based tests for service lifecycle type categorization.

Feature: extended-coverage-and-health-integration, Property 4: Service lifecycle type categorization

**Validates: Requirements 2.3**

Tests that categorize_service() ALWAYS returns exactly one of the 5 valid categories
for any input, and that known URL patterns map to expected categories deterministically.
"""
import sys
import os

# Add scripts directory to path for imports
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts"
))

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from discover_services import categorize_service, CATEGORIZATION_PATTERNS

# The 5 valid categories
VALID_CATEGORIES = frozenset([
    "runtime_versions",
    "engine_versions",
    "platform_versions",
    "ml_models",
    "protocol_versions",
])

# Runtime keywords that should map to runtime_versions
RUNTIME_KEYWORDS = ["lambda-runtimes", "runtime", "runtimes", "lambda-edge-runtime", "platforms-schedule"]

# Engine keywords that should map to engine_versions
ENGINE_KEYWORDS = ["engine-versions", "supported-kafka", "cluster-versions", "glue-version", "release-components"]


# --- Strategies ---

# Strategy for arbitrary service names
service_name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "Pd")),
    min_size=1,
    max_size=50,
)

# Strategy for arbitrary URLs (both matching and non-matching patterns)
arbitrary_url_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "Pd", "Ps")),
    min_size=0,
    max_size=200,
)

# Strategy for URLs that look like AWS documentation URLs
aws_doc_url_strategy = st.builds(
    lambda path: f"https://docs.aws.amazon.com/{path}",
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "Pd")),
        min_size=1,
        max_size=100,
    ),
)

# Strategy for URLs containing runtime keywords
runtime_url_strategy = st.builds(
    lambda prefix, keyword, suffix: f"https://docs.aws.amazon.com/{prefix}/{keyword}/{suffix}.html",
    st.text(alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz-"), min_size=1, max_size=20),
    st.sampled_from(RUNTIME_KEYWORDS),
    st.text(alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz-"), min_size=1, max_size=20),
)

# Strategy for URLs containing engine keywords
engine_url_strategy = st.builds(
    lambda prefix, keyword, suffix: f"https://docs.aws.amazon.com/{prefix}/{keyword}/{suffix}.html",
    st.text(alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz-"), min_size=1, max_size=20),
    st.sampled_from(ENGINE_KEYWORDS),
    st.text(alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz-"), min_size=1, max_size=20),
)


# --- Property Tests ---

class TestServiceCategorizationProperty:
    """
    Feature: extended-coverage-and-health-integration, Property 4: Service lifecycle type categorization

    For any URL of AWS documentation matching a known pattern, the categorize_service
    function SHALL return exactly one of the valid categories: runtime_versions,
    engine_versions, platform_versions, ml_models, or protocol_versions.
    """

    @given(
        service_name=service_name_strategy,
        url=arbitrary_url_strategy,
    )
    @settings(max_examples=150)
    def test_always_returns_valid_category(self, service_name, url):
        """
        For ANY service_name and URL (including arbitrary/random inputs),
        categorize_service() MUST return exactly one of the 5 valid categories.

        **Validates: Requirements 2.3**
        """
        result = categorize_service(service_name, url)
        assert result in VALID_CATEGORIES, (
            f"categorize_service({service_name!r}, {url!r}) returned {result!r}, "
            f"which is not in {VALID_CATEGORIES}"
        )

    @given(
        service_name=service_name_strategy,
        url=aws_doc_url_strategy,
    )
    @settings(max_examples=150)
    def test_aws_doc_urls_return_valid_category(self, service_name, url):
        """
        For any URL that looks like an AWS documentation URL,
        categorize_service() MUST return exactly one valid category.

        **Validates: Requirements 2.3**
        """
        result = categorize_service(service_name, url)
        assert result in VALID_CATEGORIES

    @given(
        service_name=service_name_strategy,
        url=runtime_url_strategy,
    )
    @settings(max_examples=100)
    def test_runtime_keywords_map_to_runtime_versions(self, service_name, url):
        """
        URLs containing runtime keywords (e.g., 'lambda-runtimes', 'runtime')
        SHALL map to runtime_versions category.

        **Validates: Requirements 2.3**
        """
        # Only test URLs where runtime keyword is the dominant signal
        # (no other category keywords present that would outscore runtime)
        url_lower = url.lower()
        other_keywords_present = any(
            kw in url_lower
            for cat, keywords in CATEGORIZATION_PATTERNS.items()
            if cat != "runtime_versions"
            for kw in keywords
        )
        assume(not other_keywords_present)

        result = categorize_service(service_name, url)
        assert result == "runtime_versions", (
            f"URL with runtime keyword returned {result!r} instead of 'runtime_versions': {url}"
        )

    @given(
        service_name=service_name_strategy,
        url=engine_url_strategy,
    )
    @settings(max_examples=100)
    def test_engine_keywords_map_to_engine_versions(self, service_name, url):
        """
        URLs containing engine keywords (e.g., 'engine-versions', 'supported-kafka')
        SHALL map to engine_versions category.

        **Validates: Requirements 2.3**
        """
        # Only test URLs where engine keyword is the dominant signal
        url_lower = url.lower()
        other_keywords_present = any(
            kw in url_lower
            for cat, keywords in CATEGORIZATION_PATTERNS.items()
            if cat != "engine_versions"
            for kw in keywords
        )
        assume(not other_keywords_present)

        result = categorize_service(service_name, url)
        assert result == "engine_versions", (
            f"URL with engine keyword returned {result!r} instead of 'engine_versions': {url}"
        )

    @given(
        service_name=service_name_strategy,
        url=arbitrary_url_strategy,
    )
    @settings(max_examples=150)
    def test_determinism_same_inputs_same_output(self, service_name, url):
        """
        categorize_service() MUST be deterministic: same inputs always produce
        the same output across multiple calls.

        **Validates: Requirements 2.3**
        """
        result1 = categorize_service(service_name, url)
        result2 = categorize_service(service_name, url)
        result3 = categorize_service(service_name, url)
        assert result1 == result2 == result3, (
            f"Non-deterministic results for ({service_name!r}, {url!r}): "
            f"{result1!r}, {result2!r}, {result3!r}"
        )
