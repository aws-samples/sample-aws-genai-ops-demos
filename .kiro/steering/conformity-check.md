---
inclusion: manual
---

# Demo Conformity Audit

Audit the current demo against all rules in `contributor-guide.md` and `solution-adoption-tracking.md`.

Check every file in the demo directory. Report findings in this format:

```
## Conformity Audit Report: [Demo Name]

### ✅ Compliant
- [List items that pass]

### ❌ Non-Compliant
| Priority | Area | Issue | Fix Required |
|----------|------|-------|--------------|
| Critical | ...  | ...   | ...          |
| High     | ...  | ...   | ...          |
| Low      | ...  | ...   | ...          |

### Summary
X items compliant, Y items require attention (Z critical, W high, V low).
```

After presenting the report, ask if the user wants any non-compliant items fixed automatically.
