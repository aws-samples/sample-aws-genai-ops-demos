# Documentation Map

This document provides a clear map of all documentation files and their relationships.

## User-Facing Documentation

These are the primary documents users should read:

### Getting Started
- **[README.md](../README.md)** - Main entry point, features, installation
- **[QUICKSTART.md](../QUICKSTART.md)** - Development setup and testing
- **[CONTRIBUTING.md](../CONTRIBUTING.md)** - How to add new detectors

### Architecture & Design
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System design and components
- **[DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md)** - Core philosophy (Dynamic Over Static, Composable, etc.)
- **[MCP_FEATURES.md](MCP_FEATURES.md)** - Tools, Resources, Prompts usage

### Detectors (What We Scan For)

#### Core Detectors
- **[bedrock-detector.md](bedrock-detector.md)** - Bedrock API usage, model detection, **prompt routing**
- **[prompt-engineering.md](prompt-engineering.md)** - Recurring prompts, quality analysis, **Nova caching (90% savings)**
- **[VSC_DETECTOR.md](VSC_DETECTOR.md)** - JSON optimization, **up to 75% token reduction**, prompt analysis
- **[agentcore-runtime.md](agentcore-runtime.md)** - Lifecycle configuration (idle timeout, max lifetime), deployment patterns, streaming, async processing, session management
- **[cross-cutting-patterns.md](cross-cutting-patterns.md)** - Multi-service cost impacts

#### Anti-Patterns
- **[CACHING_CROSS_REGION_ANTIPATTERN.md](CACHING_CROSS_REGION_ANTIPATTERN.md)** - Avoid 50%+ cost increases

### Advanced Topics
- **[GENERIC_MODEL_DETECTION.md](GENERIC_MODEL_DETECTION.md)** - Future-proof model detection (all providers)
- **[NOVA_PROMPT_CACHING_STRATEGY.md](NOVA_PROMPT_CACHING_STRATEGY.md)** - Extended examples and implementation details
- **[STATIC_VS_DYNAMIC_PROMPTS.md](STATIC_VS_DYNAMIC_PROMPTS.md)** - Prompt staticness analysis
- **[SYSTEM_PROMPT_DYNAMIC_VARIABLES.md](SYSTEM_PROMPT_DYNAMIC_VARIABLES.md)** - System prompt variable detection

## Implementation Detail Documents

These documents describe implementation details and are primarily for maintainers:

### Status & Progress
- **[PROJECT_STATUS.md](PROJECT_STATUS.md)** - Current capabilities and roadmap
- **[IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md)** - What we built and why

### Implementation Details
- **[DETECTOR_IMPROVEMENTS.md](DETECTOR_IMPROVEMENTS.md)** - AWS Strands support and enhancements
- **[CROSS_REGION_DETECTION_IMPROVEMENTS.md](CROSS_REGION_DETECTION_IMPROVEMENTS.md)** - Cross-region inference improvements
- **[ENHANCED_ENRICHMENT_INSTRUCTIONS.md](ENHANCED_ENRICHMENT_INSTRUCTIONS.md)** - Auto follow-up improvements
- **[MODEL_RECOMMENDATION_FIX.md](MODEL_RECOMMENDATION_FIX.md)** - Dynamic discovery vs hardcoded recommendations
- **[FALSE_POSITIVE_SUMMARY.md](FALSE_POSITIVE_SUMMARY.md)** - Quick overview of false positive mitigation (start here)
- **[FALSE_POSITIVE_MITIGATION_ANALYSIS.md](FALSE_POSITIVE_MITIGATION_ANALYSIS.md)** - Detailed analysis of 3 approaches
- **[FALSE_POSITIVE_RECOMMENDATION.md](FALSE_POSITIVE_RECOMMENDATION.md)** - Recommended approach for false positive filtering
- **[FALSE_POSITIVE_IMPLEMENTATION.md](FALSE_POSITIVE_IMPLEMENTATION.md)** - Implementation summary and results

### Completion Status Documents
- **[STATIC_PROMPT_DETECTION_COMPLETE.md](STATIC_PROMPT_DETECTION_COMPLETE.md)** - Static vs dynamic implementation status
- **[SYSTEM_PROMPT_DETECTION_COMPLETE.md](SYSTEM_PROMPT_DETECTION_COMPLETE.md)** - System prompt variable detection status
- **[PROMPT_CACHING_COMPLETE_REFERENCE.md](PROMPT_CACHING_COMPLETE_REFERENCE.md)** - Technical reference for caching

## Consolidated Content

Some documents have been consolidated to reduce duplication:

### Merged Into Parent Documents
- **VSC_PROMPT_DETECTION.md** → Consolidated into [VSC_DETECTOR.md](VSC_DETECTOR.md#prompt-analysis-enhancement)

### Implementation Summaries
- **[PROMPT_ROUTING_IMPLEMENTATION.md](PROMPT_ROUTING_IMPLEMENTATION.md)** - Implementation summary for prompt routing feature (references [bedrock-detector.md](bedrock-detector.md#prompt-routing))

## Documentation Hierarchy

```
README.md (Start Here)
├── Getting Started
│   ├── QUICKSTART.md
│   ├── MCP_FEATURES.md
│   └── CONTRIBUTING.md
│
├── Architecture
│   ├── ARCHITECTURE.md
│   └── DESIGN_PRINCIPLES.md
│
├── Detectors (User-Facing)
│   ├── bedrock-detector.md (includes prompt routing)
│   ├── prompt-engineering.md (includes Nova caching)
│   ├── VSC_DETECTOR.md (includes prompt analysis)
│   ├── agentcore-runtime.md (includes lifecycle configuration)
│   ├── cross-cutting-patterns.md
│   └── CACHING_CROSS_REGION_ANTIPATTERN.md
│
├── Advanced Topics
│   ├── GENERIC_MODEL_DETECTION.md
│   ├── NOVA_PROMPT_CACHING_STRATEGY.md (extended examples)
│   ├── STATIC_VS_DYNAMIC_PROMPTS.md
│   └── SYSTEM_PROMPT_DYNAMIC_VARIABLES.md
│
└── Implementation Details (Maintainers)
    ├── PROJECT_STATUS.md
    ├── IMPLEMENTATION_SUMMARY.md
    ├── DETECTOR_IMPROVEMENTS.md
    ├── CROSS_REGION_DETECTION_IMPROVEMENTS.md
    ├── ENHANCED_ENRICHMENT_INSTRUCTIONS.md
    ├── MODEL_RECOMMENDATION_FIX.md
    ├── PROMPT_ROUTING_IMPLEMENTATION.md (prompt routing feature)
    ├── STATIC_PROMPT_DETECTION_COMPLETE.md
    ├── SYSTEM_PROMPT_DETECTION_COMPLETE.md
    └── PROMPT_CACHING_COMPLETE_REFERENCE.md
```

## Quick Reference

**Want to understand what the scanner detects?**
→ Start with [README.md](../README.md) detector table, then read specific detector docs

**Want to understand how it works?**
→ Read [ARCHITECTURE.md](ARCHITECTURE.md) and [DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md)

**Want to add a new detector?**
→ Read [CONTRIBUTING.md](../CONTRIBUTING.md)

**Want to understand a specific optimization?**
→ Check the detector docs (bedrock, prompt-engineering, VSC, etc.)

**Want implementation details?**
→ Check the implementation detail documents

## Maintenance Notes

### When Adding New Features
1. Update the relevant detector document (user-facing)
2. Add entry to README.md detector table
3. Create implementation detail doc if needed (for complex features)
4. Update this map

### When Consolidating Content
1. Move content to appropriate parent document
2. Add consolidation note to old file
3. Update links in README.md
4. Update this map

### Document Lifecycle
- **User-Facing:** Keep updated, these are the primary docs
- **Implementation Details:** Can become outdated, mark as historical if needed
- **Status Documents:** Archive when feature is complete and stable
