from artifact_workflow_runtime.evidence import EvidenceExtractor


def test_strict_extract_accepts_observe_packet_with_top_level_sections_and_structured_domain_facts():
    text = """Here is the structured observation packet:

```json
{
  "summary": "obs",
  "structured_evidence": {
    "repository_root": "/workspace/project",
    "grpc_csharp_exists": false,
    "proto_definitions": {"service_name": "Freeplane"}
  },
  "commands_run": [
    {"command": "ls -la /workspace/project/grpc"}
  ],
  "files_observed": [
    {"path": "grpc/python/pyproject.toml", "summary": "Python build config"}
  ],
  "blockers": [
    {"summary": "grpc/csharp directory does not exist yet", "severity": "high", "blocker_kind": "missing_dependency"}
  ],
  "unknowns": [
    "What .NET SDK version should be targeted?"
  ]
}
```
"""
    evidence = EvidenceExtractor().from_agent_output(text, strict=True)
    assert evidence.commands_run
    assert evidence.files_observed
    assert evidence.blockers
    assert evidence.extracted_facts
    subjects = {item.subject for item in evidence.extracted_facts}
    assert "repository_root" in subjects
    assert "grpc_csharp_exists" in subjects
