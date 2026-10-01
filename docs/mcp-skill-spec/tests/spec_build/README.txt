Sources of ../../OPENJEV_MCP_SKILLS_SPEC.md (run from this directory):
  python3 gen_examples.py            # writes ../cases/00-spec-examples.json (every HTTP example in the spec)
  ../../../../.venv/bin/python capture.py   # runs them live, writes captured.json (full bodies, latency)
  ../../../../.venv/bin/python render.py p1.md p2.md p3.md p4.md p5.md p6.md p7.md ../../OPENJEV_MCP_SKILLS_SPEC.md
Placeholders in p*.md: {{REQ:id}} {{RESP:id}} {{RESPTOP:id:n}} {{OUT:name}} {{V:id.question.field}}.
Edit p*.md, not the rendered spec, or the next render overwrites the edit.
