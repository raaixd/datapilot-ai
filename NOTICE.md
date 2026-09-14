# Notice / Acknowledgements

DataPilot AI was inspired by, and used as a starting point for understanding
the problem space of, the open-source project:

**AI-data-analyst** by NeejiMed
https://github.com/NeejiMed/AI-data-analyst
Licensed under the MIT License.

No source files from that repository were copied into this project. This is
an independent implementation with a different architecture (see
`README.md` -> "How this differs from the reference project" for specifics):
a CSV-upload-first workflow instead of a fixed synthetic schema, SQLite as
the default analytical engine instead of SQLAlchemy + PostgreSQL, no
RAG/vector-store layer, a from-scratch deterministic SQL validator, a
from-scratch data-quality profiler, a mock-LLM-driven automated test suite
and evaluation harness, and its own report/visualization modules.

The MIT License permits this kind of derivative work; this notice exists to
give credit to the original author for the architectural idea and to avoid
any implication that NeejiMed authored or endorses this project.

DataPilot AI is itself released under the MIT License -- see `LICENSE`.
