"""
execute.py 리팩터링 안전망 테스트.
리팩터링 전후 동작이 동일한지 검증한다.
"""

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import execute as ex


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_project(tmp_path):
    """phases/, CLAUDE.md, docs/ 를 갖춘 임시 프로젝트 구조."""
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()

    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Rules\n- rule one\n- rule two")

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "arch.md").write_text("# Architecture\nSome content")
    (docs_dir / "guide.md").write_text("# Guide\nAnother doc")

    return tmp_path


@pytest.fixture
def phase_dir(tmp_project):
    """step 3개를 가진 phase 디렉토리."""
    d = tmp_project / "phases" / "0-mvp"
    d.mkdir()

    index = {
        "project": "TestProject",
        "phase": "mvp",
        "steps": [
            {"step": 0, "name": "setup", "type": "chore", "scope": "db",
             "desc": "로컬 컨테이너와 마이그레이션 러너",
             "status": "completed", "summary": "프로젝트 초기화 완료"},
            {"step": 1, "name": "core", "type": "feat", "scope": "db",
             "desc": "문서 변경 트리거와 임베딩 잡 생성",
             "status": "completed", "summary": "핵심 로직 구현"},
            {"step": 2, "name": "ui", "type": "feat", "scope": "frontend",
             "desc": "문서 목록 화면", "status": "pending"},
        ],
    }
    (d / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False))
    (d / "step2.md").write_text("# Step 2: UI\n\nUI를 구현하세요.")

    return d


@pytest.fixture
def top_index(tmp_project):
    """phases/index.json (top-level)."""
    top = {
        "phases": [
            {"dir": "0-mvp", "status": "pending"},
            {"dir": "1-polish", "status": "pending"},
        ]
    }
    p = tmp_project / "phases" / "index.json"
    p.write_text(json.dumps(top, indent=2))
    return p


@pytest.fixture
def executor(tmp_project, phase_dir):
    """테스트용 StepExecutor 인스턴스. git 호출은 별도 mock 필요."""
    with patch.object(ex, "ROOT", tmp_project):
        inst = ex.StepExecutor("0-mvp")
    # 내부 경로를 tmp_project 기준으로 재설정
    inst._root = str(tmp_project)
    inst._phases_dir = tmp_project / "phases"
    inst._phase_dir = phase_dir
    inst._phase_dir_name = "0-mvp"
    inst._index_file = phase_dir / "index.json"
    inst._top_index_file = tmp_project / "phases" / "index.json"
    return inst


# ---------------------------------------------------------------------------
# _stamp (= 이전 now_iso)
# ---------------------------------------------------------------------------

class TestStamp:
    def test_returns_kst_timestamp(self, executor):
        result = executor._stamp()
        assert "+0900" in result

    def test_format_is_iso(self, executor):
        result = executor._stamp()
        dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%S%z")
        assert dt.tzinfo is not None

    def test_is_current_time(self, executor):
        before = datetime.now(ex.StepExecutor.TZ).replace(microsecond=0)
        result = executor._stamp()
        after = datetime.now(ex.StepExecutor.TZ).replace(microsecond=0) + timedelta(seconds=1)
        parsed = datetime.strptime(result, "%Y-%m-%dT%H:%M:%S%z")
        assert before <= parsed <= after


# ---------------------------------------------------------------------------
# _read_json / _write_json
# ---------------------------------------------------------------------------

class TestJsonHelpers:
    def test_roundtrip(self, tmp_path):
        data = {"key": "값", "nested": [1, 2, 3]}
        p = tmp_path / "test.json"
        ex.StepExecutor._write_json(p, data)
        loaded = ex.StepExecutor._read_json(p)
        assert loaded == data

    def test_save_ensures_ascii_false(self, tmp_path):
        p = tmp_path / "test.json"
        ex.StepExecutor._write_json(p, {"한글": "테스트"})
        raw = p.read_text()
        assert "한글" in raw
        assert "\\u" not in raw

    def test_save_indented(self, tmp_path):
        p = tmp_path / "test.json"
        ex.StepExecutor._write_json(p, {"a": 1})
        raw = p.read_text()
        assert "\n" in raw

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ex.StepExecutor._read_json(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# _load_guardrails
# ---------------------------------------------------------------------------

class TestLoadGuardrails:
    def test_loads_claude_md_and_docs(self, executor, tmp_project):
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_guardrails()
        assert "# Rules" in result
        assert "rule one" in result
        assert "# Architecture" in result
        assert "# Guide" in result

    def test_sections_separated_by_divider(self, executor, tmp_project):
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_guardrails()
        assert "---" in result

    def test_docs_sorted_alphabetically(self, executor, tmp_project):
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_guardrails()
        arch_pos = result.index("arch")
        guide_pos = result.index("guide")
        assert arch_pos < guide_pos

    def test_no_claude_md(self, executor, tmp_project):
        (tmp_project / "CLAUDE.md").unlink()
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_guardrails()
        assert "CLAUDE.md" not in result
        assert "Architecture" in result

    def test_no_docs_dir(self, executor, tmp_project):
        import shutil
        shutil.rmtree(tmp_project / "docs")
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_guardrails()
        assert "Rules" in result
        assert "Architecture" not in result

    def test_empty_project(self, tmp_path):
        with patch.object(ex, "ROOT", tmp_path):
            # executor가 필요 없는 static-like 동작이므로 임시 인스턴스
            phases_dir = tmp_path / "phases" / "dummy"
            phases_dir.mkdir(parents=True)
            idx = {"project": "T", "phase": "t", "steps": []}
            (phases_dir / "index.json").write_text(json.dumps(idx))
            inst = ex.StepExecutor.__new__(ex.StepExecutor)
            result = inst._load_guardrails()
        assert result == ""


# ---------------------------------------------------------------------------
# _build_step_context
# ---------------------------------------------------------------------------

class TestBuildStepContext:
    def test_includes_completed_with_summary(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        result = ex.StepExecutor._build_step_context(index)
        assert "Step 0 (setup): 프로젝트 초기화 완료" in result
        assert "Step 1 (core): 핵심 로직 구현" in result

    def test_excludes_pending(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        result = ex.StepExecutor._build_step_context(index)
        assert "ui" not in result

    def test_excludes_completed_without_summary(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        del index["steps"][0]["summary"]
        result = ex.StepExecutor._build_step_context(index)
        assert "setup" not in result
        assert "core" in result

    def test_empty_when_no_completed(self):
        index = {"steps": [{"step": 0, "name": "a", "status": "pending"}]}
        result = ex.StepExecutor._build_step_context(index)
        assert result == ""

    def test_has_header(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        result = ex.StepExecutor._build_step_context(index)
        assert result.startswith("## 이전 Step 산출물")


# ---------------------------------------------------------------------------
# _build_preamble
# ---------------------------------------------------------------------------

class TestBuildPreamble:
    def test_includes_project_name(self, executor):
        result = executor._build_preamble("", "")
        assert "TestProject" in result

    def test_includes_guardrails(self, executor):
        result = executor._build_preamble("GUARD_CONTENT", "")
        assert "GUARD_CONTENT" in result

    def test_includes_step_context(self, executor):
        ctx = "## 이전 Step 산출물\n\n- Step 0: done"
        result = executor._build_preamble("", ctx)
        assert "이전 Step 산출물" in result

    def test_instructs_not_to_commit(self, executor):
        """커밋은 하네스가 한다 — 메시지 규칙을 한 곳에서만 관리하기 위함."""
        result = executor._build_preamble("", "")
        assert "커밋하지" in result
        # phase 이름을 스코프로 쓰는 예시를 세션에 주지 않는다
        assert "feat(mvp)" not in result

    def test_includes_rules(self, executor):
        result = executor._build_preamble("", "")
        assert "작업 규칙" in result
        assert "AC" in result

    def test_no_retry_section_by_default(self, executor):
        result = executor._build_preamble("", "")
        assert "이전 시도 실패" not in result

    def test_retry_section_with_prev_error(self, executor):
        result = executor._build_preamble("", "", prev_error="타입 에러 발생")
        assert "이전 시도 실패" in result
        assert "타입 에러 발생" in result

    def test_includes_max_retries(self, executor):
        result = executor._build_preamble("", "")
        assert str(ex.StepExecutor.MAX_RETRIES) in result

    def test_includes_index_path(self, executor):
        result = executor._build_preamble("", "")
        assert "/phases/0-mvp/index.json" in result


# ---------------------------------------------------------------------------
# _update_top_index
# ---------------------------------------------------------------------------

class TestUpdateTopIndex:
    def test_completed(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("completed")
        data = json.loads(top_index.read_text())
        mvp = next(p for p in data["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "completed"
        assert "completed_at" in mvp

    def test_error(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("error")
        data = json.loads(top_index.read_text())
        mvp = next(p for p in data["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "error"
        assert "failed_at" in mvp

    def test_blocked(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("blocked")
        data = json.loads(top_index.read_text())
        mvp = next(p for p in data["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "blocked"
        assert "blocked_at" in mvp

    def test_other_phases_unchanged(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("completed")
        data = json.loads(top_index.read_text())
        polish = next(p for p in data["phases"] if p["dir"] == "1-polish")
        assert polish["status"] == "pending"

    def test_nonexistent_dir_is_noop(self, executor, top_index):
        executor._top_index_file = top_index
        executor._phase_dir_name = "no-such-dir"
        original = json.loads(top_index.read_text())
        executor._update_top_index("completed")
        after = json.loads(top_index.read_text())
        for p_before, p_after in zip(original["phases"], after["phases"]):
            assert p_before["status"] == p_after["status"]

    def test_no_top_index_file(self, executor, tmp_path):
        executor._top_index_file = tmp_path / "nonexistent.json"
        executor._update_top_index("completed")  # should not raise


# ---------------------------------------------------------------------------
# _checkout_branch (mocked)
# ---------------------------------------------------------------------------

class TestCheckoutBranch:
    def _mock_git(self, executor, responses):
        call_idx = {"i": 0}
        def fake_git(*args):
            idx = call_idx["i"]
            call_idx["i"] += 1
            if idx < len(responses):
                return responses[idx]
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

    def test_already_on_branch(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="feat/mvp\n", stderr=""),
        ])
        executor._checkout_branch()  # should return without checkout

    def test_branch_name_is_single_source(self, executor):
        """브랜치명이 checkout과 push 두 곳에 하드코딩되면 한쪽만 고쳐져 어긋난다."""
        assert executor._branch_name() == "feat/mvp"

    def test_push_uses_same_branch_as_checkout(self, executor):
        """--push가 checkout한 것과 다른 브랜치를 밀면 실패한다."""
        calls = []
        def fake_git(*args):
            calls.append(args)
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git
        executor._auto_push = True

        executor._finalize()

        push = next(c for c in calls if c[0] == "push")
        assert push[-1] == executor._branch_name()
        assert "feat-mvp" not in push

    def test_branch_name_uses_slash_prefix(self, executor):
        """CLAUDE.md 브랜치 규칙은 feat/ 슬래시 접두사다 (ADR-013)."""
        calls = []
        def fake_git(*args):
            calls.append(args)
            if args[:2] == ("rev-parse", "--abbrev-ref"):
                return MagicMock(returncode=0, stdout="main\n", stderr="")
            if args[:2] == ("rev-parse", "--verify"):
                return MagicMock(returncode=1, stdout="", stderr="not found")
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

        executor._checkout_branch()

        checkout = next(c for c in calls if c[0] == "checkout")
        assert "feat/mvp" in checkout
        assert not any("feat-mvp" in str(c) for c in calls)

    def test_branch_exists_checkout(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="main\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ])
        executor._checkout_branch()

    def test_branch_not_exists_create(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="main\n", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="not found"),
            MagicMock(returncode=0, stdout="", stderr=""),
        ])
        executor._checkout_branch()

    def test_checkout_fails_exits(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="main\n", stderr=""),
            MagicMock(returncode=1, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="dirty tree"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            executor._checkout_branch()
        assert exc_info.value.code == 1

    def test_no_git_exits(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=1, stdout="", stderr="not a git repo"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            executor._checkout_branch()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _commit_step (mocked)
# ---------------------------------------------------------------------------

class TestBuildCommitMsg:
    """커밋 메시지는 step의 type·scope·desc로 조립한다 (CLAUDE.md 커밋 규칙)."""

    def test_uses_type_scope_desc(self):
        step = {"step": 1, "name": "core", "type": "feat", "scope": "db",
                "desc": "문서 변경 트리거와 임베딩 잡 생성"}
        assert ex.StepExecutor._build_commit_msg(step) == \
            "feat(db): 문서 변경 트리거와 임베딩 잡 생성"

    def test_type_is_not_always_feat(self):
        """설정·구조 작업은 chore·refactor여야 한다."""
        step = {"step": 0, "name": "setup", "type": "chore", "scope": "db",
                "desc": "로컬 컨테이너 구성"}
        assert ex.StepExecutor._build_commit_msg(step).startswith("chore(db):")

    def test_type_defaults_to_feat(self):
        step = {"step": 1, "name": "core", "scope": "api", "desc": "문서 CRUD"}
        assert ex.StepExecutor._build_commit_msg(step) == "feat(api): 문서 CRUD"

    def test_scope_omitted_when_absent(self):
        """스코프는 Conventional Commits에서 선택이다."""
        step = {"step": 1, "name": "core", "type": "feat", "desc": "무언가"}
        assert ex.StepExecutor._build_commit_msg(step) == "feat: 무언가"

    def test_falls_back_to_step_name_without_desc(self):
        step = {"step": 3, "name": "worker", "type": "feat", "scope": "worker"}
        assert ex.StepExecutor._build_commit_msg(step) == "feat(worker): step 3 — worker"

    def test_never_uses_phase_name_as_scope(self):
        """feat(m1-db-layer)는 CLAUDE.md 허용 스코프 7종이 아니다."""
        step = {"step": 2, "name": "ui", "type": "feat", "scope": "frontend",
                "desc": "문서 목록 화면"}
        msg = ex.StepExecutor._build_commit_msg(step)
        assert "mvp" not in msg and "m1-" not in msg


class TestCommitStep:
    def test_two_phase_commit(self, executor):
        calls = []
        def fake_git(*args):
            calls.append(args)
            if args[:2] == ("diff", "--cached"):
                return MagicMock(returncode=1)
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

        step = {"step": 2, "name": "ui", "type": "feat", "scope": "frontend",
                "desc": "문서 목록 화면"}
        executor._commit_step(step)

        commit_calls = [c for c in calls if c[0] == "commit"]
        assert len(commit_calls) == 2
        assert commit_calls[0][2] == "feat(frontend): 문서 목록 화면"
        # 산출물 커밋에는 스코프를 붙이지 않는다 — 허용 스코프 목록에 해당 항목이 없다
        assert commit_calls[1][2].startswith("chore:")
        assert "chore(mvp)" not in commit_calls[1][2]

    def test_no_code_changes_skips_code_commit(self, executor):
        call_count = {"diff": 0}
        calls = []
        def fake_git(*args):
            calls.append(args)
            if args[:2] == ("diff", "--cached"):
                call_count["diff"] += 1
                if call_count["diff"] == 1:
                    return MagicMock(returncode=0)
                return MagicMock(returncode=1)
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

        step = {"step": 2, "name": "ui", "type": "feat", "scope": "frontend",
                "desc": "문서 목록 화면"}
        executor._commit_step(step)

        commit_msgs = [c[2] for c in calls if c[0] == "commit"]
        assert len(commit_msgs) == 1
        assert commit_msgs[0].startswith("chore:")


# ---------------------------------------------------------------------------
# _invoke_claude (mocked)
# ---------------------------------------------------------------------------

class TestInvokeClaude:
    def test_invokes_claude_with_correct_args(self, executor):
        mock_result = MagicMock(returncode=0, stdout='{"result": "ok"}', stderr="")
        step = {"step": 2, "name": "ui"}
        preamble = "PREAMBLE\n"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            output = executor._invoke_claude(step, preamble)

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "--output-format" in cmd
        assert "PREAMBLE" in cmd[-1]
        assert "UI를 구현하세요" in cmd[-1]

    def test_saves_output_json(self, executor):
        mock_result = MagicMock(returncode=0, stdout='{"ok": true}', stderr="")
        step = {"step": 2, "name": "ui"}

        with patch("subprocess.run", return_value=mock_result):
            executor._invoke_claude(step, "preamble")

        output_file = executor._phase_dir / "step2-output.json"
        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert data["step"] == 2
        assert data["name"] == "ui"
        assert data["exitCode"] == 0
        assert data["agent"] == "claude"

    def test_nonexistent_step_file_exits(self, executor):
        step = {"step": 99, "name": "nonexistent"}
        with pytest.raises(SystemExit) as exc_info:
            executor._invoke_claude(step, "preamble")
        assert exc_info.value.code == 1

    def test_timeout_is_1800(self, executor):
        mock_result = MagicMock(returncode=0, stdout="{}", stderr="")
        step = {"step": 2, "name": "ui"}

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            executor._invoke_claude(step, "preamble")

        assert mock_run.call_args[1]["timeout"] == 1800


# ---------------------------------------------------------------------------
# 스텝 세션 모델 지정
# ---------------------------------------------------------------------------

class TestModelSelection:
    def test_default_model_is_opus(self, executor):
        assert executor._model == "opus"

    def test_constructor_accepts_model(self, tmp_project, phase_dir):
        with patch.object(ex, "ROOT", tmp_project):
            inst = ex.StepExecutor("0-mvp", model="sonnet")
        assert inst._model == "sonnet"

    def test_invoke_passes_model_flag(self, executor):
        executor._model = "sonnet"
        mock_result = MagicMock(returncode=0, stdout="{}", stderr="")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            executor._invoke_claude({"step": 2, "name": "ui"}, "preamble")

        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "sonnet"

    def test_main_defaults_to_opus(self):
        with patch("sys.argv", ["execute.py", "0-mvp"]):
            with patch.object(ex, "StepExecutor") as mock_exec:
                ex.main()
        assert mock_exec.call_args[1]["model"] == "opus"

    def test_main_passes_model_flag(self):
        with patch("sys.argv", ["execute.py", "0-mvp", "--model", "haiku"]):
            with patch.object(ex, "StepExecutor") as mock_exec:
                ex.main()
        assert mock_exec.call_args[1]["model"] == "haiku"


# ---------------------------------------------------------------------------
# _invoke_codex (mocked)
# ---------------------------------------------------------------------------

class TestInvokeCodex:
    def test_invokes_codex_with_correct_args(self, executor):
        mock_result = MagicMock(returncode=0, stdout='{"result": "ok"}', stderr="")
        step = {"step": 2, "name": "ui"}
        preamble = "PREAMBLE\n"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            executor._invoke_codex(step, preamble)

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "codex"
        assert cmd[1] == "exec"
        assert "--json" in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "PREAMBLE" in cmd[-1]
        assert "UI를 구현하세요" in cmd[-1]

    def test_saves_output_json(self, executor):
        mock_result = MagicMock(returncode=0, stdout='{"ok": true}', stderr="")
        step = {"step": 2, "name": "ui"}

        with patch("subprocess.run", return_value=mock_result):
            executor._invoke_codex(step, "preamble")

        output_file = executor._phase_dir / "step2-output.json"
        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert data["step"] == 2
        assert data["name"] == "ui"
        assert data["exitCode"] == 0
        assert data["agent"] == "codex"

    def test_nonexistent_step_file_exits(self, executor):
        step = {"step": 99, "name": "nonexistent"}
        with pytest.raises(SystemExit) as exc_info:
            executor._invoke_codex(step, "preamble")
        assert exc_info.value.code == 1

    def test_timeout_is_1800(self, executor):
        mock_result = MagicMock(returncode=0, stdout="{}", stderr="")
        step = {"step": 2, "name": "ui"}

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            executor._invoke_codex(step, "preamble")

        assert mock_run.call_args[1]["timeout"] == 1800


# ---------------------------------------------------------------------------
# Codex 우선 + Claude 폴백 (sticky) — 코덱스 사용량 한도 도달 시
# 이번 phase 실행이 끝날 때까지 남은 모든 step을 Claude로 처리한다.
# ---------------------------------------------------------------------------

class TestAgentFallback:
    def test_default_active_agent_is_codex(self, executor):
        assert executor._active_agent == "codex"

    def test_invoke_agent_uses_codex_when_active(self, executor):
        mock_result = MagicMock(returncode=0, stdout='{"ok": true}', stderr="")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            executor._invoke_agent({"step": 2, "name": "ui"}, "preamble")
        assert mock_run.call_args[0][0][0] == "codex"

    def test_invoke_agent_uses_claude_when_active(self, executor):
        executor._active_agent = "claude"
        mock_result = MagicMock(returncode=0, stdout='{"ok": true}', stderr="")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            executor._invoke_agent({"step": 2, "name": "ui"}, "preamble")
        assert mock_run.call_args[0][0][0] == "claude"

    def test_quota_exceeded_switches_to_claude_and_retries_same_step(self, executor):
        codex_fail = MagicMock(returncode=1, stdout="", stderr="Error: usage limit reached, try again later")
        claude_ok = MagicMock(returncode=0, stdout='{"ok": true}', stderr="")

        with patch("subprocess.run", side_effect=[codex_fail, claude_ok]) as mock_run:
            output = executor._invoke_agent({"step": 2, "name": "ui"}, "preamble")

        assert mock_run.call_count == 2
        assert mock_run.call_args_list[0][0][0][0] == "codex"
        assert mock_run.call_args_list[1][0][0][0] == "claude"
        assert executor._active_agent == "claude"
        assert output["agent"] == "claude"

    def test_fallback_is_sticky_across_subsequent_calls(self, executor):
        codex_fail = MagicMock(returncode=1, stdout="", stderr="rate limit exceeded")
        claude_ok = MagicMock(returncode=0, stdout='{"ok": true}', stderr="")

        with patch("subprocess.run", side_effect=[codex_fail, claude_ok]):
            executor._invoke_agent({"step": 2, "name": "ui"}, "preamble")

        # 이후 호출은 codex를 재시도하지 않고 바로 claude로 간다
        with patch("subprocess.run", return_value=claude_ok) as mock_run2:
            executor._invoke_agent({"step": 2, "name": "ui"}, "preamble")
        assert mock_run2.call_count == 1
        assert mock_run2.call_args[0][0][0] == "claude"

    def test_non_quota_failure_does_not_switch_agent(self, executor):
        """quota와 무관한 실패는 codex를 유지한다 — 재시도는 상위 재시도 루프의 몫이다."""
        codex_fail = MagicMock(returncode=1, stdout="", stderr="SyntaxError: unexpected token")

        with patch("subprocess.run", return_value=codex_fail) as mock_run:
            executor._invoke_agent({"step": 2, "name": "ui"}, "preamble")

        assert executor._active_agent == "codex"
        assert mock_run.call_count == 1

    def test_quota_signals_detected_case_insensitively(self):
        for msg in ["Rate Limit Reached", "USAGE LIMIT", "429 Too Many Requests"]:
            assert ex.StepExecutor._is_quota_exceeded(
                {"exitCode": 1, "stdout": "", "stderr": msg}
            )

    def test_zero_exit_never_counts_as_quota_exceeded(self):
        assert not ex.StepExecutor._is_quota_exceeded(
            {"exitCode": 0, "stdout": "", "stderr": "usage limit mentioned but succeeded"}
        )

    def test_unrelated_failure_is_not_quota_exceeded(self):
        assert not ex.StepExecutor._is_quota_exceeded(
            {"exitCode": 1, "stdout": "", "stderr": "SyntaxError: unexpected token"}
        )


# ---------------------------------------------------------------------------
# progress_indicator (= 이전 Spinner)
# ---------------------------------------------------------------------------

class TestProgressIndicator:
    def test_context_manager(self):
        import time
        with ex.progress_indicator("test") as pi:
            time.sleep(0.15)
        assert pi.elapsed >= 0.1

    def test_elapsed_increases(self):
        import time
        with ex.progress_indicator("test") as pi:
            time.sleep(0.2)
        assert pi.elapsed > 0


# ---------------------------------------------------------------------------
# main() CLI 파싱 (mocked)
# ---------------------------------------------------------------------------

class TestMainCli:
    def test_no_args_exits(self):
        with patch("sys.argv", ["execute.py"]):
            with pytest.raises(SystemExit) as exc_info:
                ex.main()
            assert exc_info.value.code == 2  # argparse exits with 2

    def test_invalid_phase_dir_exits(self):
        with patch("sys.argv", ["execute.py", "nonexistent"]):
            with patch.object(ex, "ROOT", Path("/tmp/fake_nonexistent")):
                with pytest.raises(SystemExit) as exc_info:
                    ex.main()
                assert exc_info.value.code == 1

    def test_missing_index_exits(self, tmp_project):
        (tmp_project / "phases" / "empty").mkdir()
        with patch("sys.argv", ["execute.py", "empty"]):
            with patch.object(ex, "ROOT", tmp_project):
                with pytest.raises(SystemExit) as exc_info:
                    ex.main()
                assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _check_blockers (= 이전 main() error/blocked 체크)
# ---------------------------------------------------------------------------

class TestCheckBlockers:
    def _make_executor_with_steps(self, tmp_project, steps):
        d = tmp_project / "phases" / "test-phase"
        d.mkdir(exist_ok=True)
        index = {"project": "T", "phase": "test", "steps": steps}
        (d / "index.json").write_text(json.dumps(index))

        with patch.object(ex, "ROOT", tmp_project):
            inst = ex.StepExecutor.__new__(ex.StepExecutor)
        inst._root = str(tmp_project)
        inst._phases_dir = tmp_project / "phases"
        inst._phase_dir = d
        inst._phase_dir_name = "test-phase"
        inst._index_file = d / "index.json"
        inst._top_index_file = tmp_project / "phases" / "index.json"
        inst._phase_name = "test"
        inst._total = len(steps)
        return inst

    def test_error_step_exits_1(self, tmp_project):
        steps = [
            {"step": 0, "name": "ok", "status": "completed"},
            {"step": 1, "name": "bad", "status": "error", "error_message": "fail"},
        ]
        inst = self._make_executor_with_steps(tmp_project, steps)
        with pytest.raises(SystemExit) as exc_info:
            inst._check_blockers()
        assert exc_info.value.code == 1

    def test_blocked_step_exits_2(self, tmp_project):
        steps = [
            {"step": 0, "name": "ok", "status": "completed"},
            {"step": 1, "name": "stuck", "status": "blocked", "blocked_reason": "API key"},
        ]
        inst = self._make_executor_with_steps(tmp_project, steps)
        with pytest.raises(SystemExit) as exc_info:
            inst._check_blockers()
        assert exc_info.value.code == 2
