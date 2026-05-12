# Python Layering Design

## Goal

Reorganize the formal Python runtime and test files into a clear layered package structure without breaking the current entry commands:

- `uvicorn app:app --reload`
- `pytest -q`

The refactor must preserve current behavior while making module ownership and dependency direction explicit.

## Non-Goals

This design does not cover:

- refactoring `temp_if_use/`
- changing frontend file placement
- changing runtime behavior of scraping, analysis, CSV export, or streaming
- introducing dependency injection frameworks or new third-party libraries
- redesigning the Amazon workflow itself

## Current State

The current formal Python files are all flat at repository root:

- `app.py`
- `agent_service.py`
- `analysis_tools.py`
- `artifacts.py`
- `amazon_tools.py`
- `test_app.py`
- `test_agent_service.py`
- `test_analysis_tools.py`
- `test_artifacts.py`
- `test_amazon_tools.py`
- `test_frontend_static.py`

Current runtime dependency direction:

- `app.py` imports `agent_service` and `artifacts`
- `agent_service.py` imports `amazon_tools`, `analysis_tools`, and `artifacts`
- `analysis_tools.py` is effectively pure business logic plus LLM call wrapper
- `artifacts.py` handles CSV artifact writing
- `amazon_tools.py` handles Amazon scraping and review summarization

The code works, but root-level sprawl makes module boundaries weak and encourages future imports to bypass any architectural discipline.

## Requirements

The reorganization must satisfy all of the following:

1. Formal runtime modules move out of repository root into layered package directories.
2. Tests move out of repository root into a `tests/` tree.
3. `temp_if_use/` remains untouched and outside the formal dependency graph.
4. Current entry commands remain valid.
5. Import paths remain stable enough that runtime and tests continue to execute without path hacks.
6. Dependency direction is explicit and enforced by placement and import style.

## Target Structure

The formal code should move into one package named `mp_agent`:

```text
mp_agent/
  __init__.py
  presentation/
    __init__.py
    http.py
  application/
    __init__.py
    agent_service.py
  domain/
    __init__.py
    analysis.py
  infrastructure/
    __init__.py
    amazon.py
    artifacts.py

tests/
  presentation/
    test_app.py
  application/
    test_agent_service.py
  domain/
    test_analysis.py
  infrastructure/
    test_amazon.py
    test_artifacts.py
  ui/
    test_frontend_static.py

app.py
```

Naming adjustments:

- `analysis_tools.py` becomes `mp_agent/domain/analysis.py`
- `amazon_tools.py` becomes `mp_agent/infrastructure/amazon.py`
- `artifacts.py` becomes `mp_agent/infrastructure/artifacts.py`
- `agent_service.py` becomes `mp_agent/application/agent_service.py`
- `app.py` implementation moves into `mp_agent/presentation/http.py`

## Layer Responsibilities

### Presentation

`mp_agent/presentation/http.py`

Responsibilities:

- FastAPI app factory
- routes
- SSE response wiring
- static file serving
- HTTP request/response models

This layer can depend on:

- `application`
- `infrastructure` only for boundary-level constants if necessary

This layer must not contain Amazon workflow orchestration or analysis rules.

### Application

`mp_agent/application/agent_service.py`

Responsibilities:

- task lifecycle
- message parsing
- orchestration of scraping, review summary, analysis, and CSV writing
- preview shaping
- progress event emission

This layer can depend on:

- `domain`
- `infrastructure`

This layer must not depend on FastAPI types.

### Domain

`mp_agent/domain/analysis.py`

Responsibilities:

- analysis-row shaping
- text normalization helpers
- fallback business rules for selling points and positioning

This layer should remain the cleanest and most portable part of the codebase.

It may include the current LLM-backed analysis helper, but it must not depend on FastAPI, Playwright, or filesystem artifact paths.

### Infrastructure

`mp_agent/infrastructure/amazon.py`

Responsibilities:

- Amazon scraping
- review Excel polling flow
- Playwright/Bright Data integration

`mp_agent/infrastructure/artifacts.py`

Responsibilities:

- artifact directory configuration
- CSV header definition
- CSV file writing

Infrastructure may depend on third-party runtime libraries and filesystem/network integrations.

Infrastructure must not import presentation code.

## Dependency Rules

Allowed dependency direction:

```text
presentation -> application
application -> domain
application -> infrastructure
domain -> standard library / OpenAI client only
infrastructure -> standard library / external integrations
```

Disallowed dependency direction:

- `domain -> application`
- `domain -> presentation`
- `infrastructure -> presentation`
- `infrastructure -> application`

The current application layer imports infrastructure and domain directly. That remains valid after the move.

## Entry Command Compatibility

The repository root keeps a thin `app.py` shim so `uvicorn app:app --reload` still works.

`app.py` should only re-export the public HTTP symbols currently expected by tests:

- `app`
- `create_app`
- `build_task_event_stream`
- `ARTIFACTS_DIR`

The shim must not re-implement the real FastAPI logic.

The real implementation lives in `mp_agent/presentation/http.py`.

## Test Layout Strategy

Tests move into `tests/` and should follow the same conceptual layering as the runtime code:

- presentation tests
- application tests
- domain tests
- infrastructure tests
- frontend-static tests

Tests should import the real package modules, not the old root filenames, except for the explicit compatibility test that proves `app.py` still exports the expected HTTP entry surface.

`pytest -q` should still work from repository root without additional flags.

## Migration Strategy

The refactor should be done in controlled steps rather than one large rename.

### Step 1: Create Package Skeleton

Create:

- `mp_agent/__init__.py`
- `mp_agent/presentation/__init__.py`
- `mp_agent/application/__init__.py`
- `mp_agent/domain/__init__.py`
- `mp_agent/infrastructure/__init__.py`
- `tests/...` directories

No behavior changes yet.

### Step 2: Move Runtime Modules

Move implementations into the new package:

- `app.py` logic -> `mp_agent/presentation/http.py`
- `agent_service.py` -> `mp_agent/application/agent_service.py`
- `analysis_tools.py` -> `mp_agent/domain/analysis.py`
- `amazon_tools.py` -> `mp_agent/infrastructure/amazon.py`
- `artifacts.py` -> `mp_agent/infrastructure/artifacts.py`

Then rewrite imports to package-qualified imports.

### Step 3: Add Root Compatibility Shim

Recreate repository-root `app.py` as a minimal forwarding module:

- import public symbols from `mp_agent.presentation.http`
- expose them unchanged

No other runtime root shims should remain unless a failing test or external entrypoint proves they are still needed.

### Step 4: Move Tests

Move root tests into `tests/` and update imports:

- package imports for real implementation
- one compatibility assertion for root `app.py`

### Step 5: Remove Stale Root Runtime Modules

After tests pass against the package structure, remove obsolete root runtime modules:

- `agent_service.py`
- `analysis_tools.py`
- `amazon_tools.py`
- `artifacts.py`

Root should retain only `app.py` as the compatibility entrypoint.

## Error Handling and Risk Control

Main risks:

1. Broken imports after file moves.
2. Tests still importing old root modules.
3. Circular dependencies introduced by careless package imports.
4. Runtime path assumptions tied to `__file__` changing location after moves.

Mitigations:

1. Move modules in dependency order and update imports immediately.
2. Keep `app.py` as a stable root shim from the first migration step that affects entrypoints.
3. Preserve `artifacts` and `frontend` path calculations explicitly relative to module files or repository root as needed.
4. Run targeted tests after each migration slice, then a full `pytest -q`.

## Verification

Required verification for the completed refactor:

- `pytest tests/presentation/test_app.py -q`
- `pytest tests/application/test_agent_service.py -q`
- `pytest tests/domain/test_analysis.py -q`
- `pytest tests/infrastructure/test_artifacts.py -q`
- `pytest tests/infrastructure/test_amazon.py -q`
- `pytest tests/ui/test_frontend_static.py -q`
- `pytest -q`

Optional smoke check:

- `python -c "from app import app, create_app"`

## Success Criteria

The refactor is successful when:

1. formal runtime Python files are layered under `mp_agent/`
2. formal tests are layered under `tests/`
3. repository root no longer contains the old flat business modules
4. `temp_if_use/` remains isolated
5. `uvicorn app:app --reload` still works
6. `pytest -q` still works except for any already-existing unrelated failures
