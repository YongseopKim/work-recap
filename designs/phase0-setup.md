# Phase 0-1: 프로젝트 셋업 상세 설계

## 목적

프로젝트 스켈레톤을 구성한다. `.venv` 가상환경 기반으로 src layout, 의존성 관리,
환경변수 템플릿, data 디렉토리 구조를 잡아 이후 모듈 개발의 기반을 만든다.

---

## 가상환경

- 경로: `.venv/` (프로젝트 루트)
- Python: 3.13.0
- 모든 명령은 `.venv/bin/python`, `.venv/bin/pip`, `.venv/bin/pytest` 사용
- 활성화: `source .venv/bin/activate`

---

## 산출물

### pyproject.toml

```toml
[project]
name = "work-recap"
version = "0.1.0"
description = "GHES activity summarizer with LLM"
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.27",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "typer>=0.12",
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "openai>=1.0",
    "anthropic>=0.30",
    "jinja2>=3.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "respx>=0.21",           # httpx mock
    "coverage>=7.0",
    "ruff>=0.6",
]

[project.scripts]
recap = "workrecap.cli.main:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/workrecap"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
line-length = 100
target-version = "py312"
```

### .env.example

```
GHES_URL=https://github.example.com
GHES_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
GHES_USERNAME=your-username
LLM_PROVIDER=openai
LLM_API_KEY=sk-xxxxxxxxxxxxxxxxxxxx
LLM_MODEL=gpt-4o-mini
DATA_DIR=data
PROMPTS_DIR=prompts
```

### .gitignore 추가 항목

```
.venv/
data/
.env
__pycache__/
*.egg-info/
dist/
.ruff_cache/
.pytest_cache/
.coverage
```

### 디렉토리 생성

```
src/workrecap/__init__.py
src/workrecap/services/__init__.py
src/workrecap/infra/__init__.py
src/workrecap/api/__init__.py
src/workrecap/api/routes/  (빈 패키지)
src/workrecap/cli/__init__.py
tests/__init__.py
tests/conftest.py
tests/fixtures/
tests/unit/__init__.py
tests/integration/__init__.py
prompts/
```

### tests/conftest.py

```python
import pytest
from pathlib import Path
from workrecap.config import AppConfig


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """테스트용 격리된 data 디렉토리."""
    data_dir = tmp_path / "data"
    for sub in ["state/jobs", "raw", "normalized", "summaries"]:
        (data_dir / sub).mkdir(parents=True)
    return data_dir


@pytest.fixture
def test_config(tmp_data_dir: Path, tmp_path: Path) -> AppConfig:
    """테스트용 AppConfig. 실제 .env 파일 불필요."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    return AppConfig(
        ghes_url="https://github.example.com",
        ghes_token="test-token",
        username="testuser",
        data_dir=tmp_data_dir,
        prompts_dir=prompts_dir,
        llm_provider="openai",
        llm_api_key="test-key",
        llm_model="gpt-4o-mini",
    )
```

---

## ToDo

| # | 작업 | 검증 |
|---|---|---|
| 0.1.1 | pyproject.toml 생성 | `.venv/bin/pip install -e ".[dev]"` 성공 |
| 0.1.2 | src layout + 빈 __init__.py 생성 | `.venv/bin/python -c "import workrecap"` 성공 |
| 0.1.3 | .env.example, .gitignore (.venv/ 포함) 생성 | 파일 존재 확인 |
| 0.1.4 | tests/conftest.py + fixture 디렉토리 생성 | `.venv/bin/pytest --collect-only` 성공 |
| 0.1.5 | prompts/ 디렉토리 생성 | 디렉토리 존재 확인 |
