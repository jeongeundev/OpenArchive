"""
ship.py 테스트 — 하네스 phase를 실행 → 검증 → PR → 리뷰·수정 → CI → 머지 대기로 잇는 상태 기계.

git은 진짜로 돌린다(bare origin까지 갖춘 임시 저장소). gh·claude·execute.py·pytest·osascript만
가짜 실행기가 받는다 — 단계 전이·멈춤 조건·재개·변조 검사가 검증 대상이다.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import ship

PHASE = "m99-demo"
ISSUE = 42
PR_URL = "https://github.com/o/r/pull/7"


# ---------------------------------------------------------------------------
# 임시 저장소
# ---------------------------------------------------------------------------

def git(cwd, *args) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def write(root: Path, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def phase_index(status="completed", **extra) -> dict:
    return {
        "project": "demo", "phase": PHASE, "issue": ISSUE, "title": "feat: 데모 (#42)",
        "steps": [
            {"step": 0, "name": "api", "status": status, "summary": "foo 상수 추가"},
        ],
        **extra,
    }


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(tmp_path, "init", "-q", "-b", "main", str(work))
    git(work, "config", "user.email", "t@example.com")
    git(work, "config", "user.name", "t")
    write(work, "backend/openarchive/foo.py", "X = 1\n")
    write(work, "backend/tests/test_foo.py", "def test_x():\n    assert 1 + 1 == 2\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "init")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "-u", "origin", "main")
    git(work, "checkout", "-q", "-b", f"feat/{PHASE}")
    write_phase(work, phase_index())
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "docs: 설계")
    write(work, "backend/openarchive/foo.py", "X = 2\n")
    write(work, "backend/tests/test_foo.py", "def test_x():\n    assert 2 * 1 == 2\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "feat(api): 데모")
    return work


def write_phase(root: Path, index: dict):
    write(root, f"phases/{PHASE}/index.json", json.dumps(index, ensure_ascii=False, indent=2))


def commit_all(root: Path, msg: str):
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)


# ---------------------------------------------------------------------------
# 가짜 실행기
# ---------------------------------------------------------------------------

def ok(stdout="", code=0, stderr=""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class FakeRunner:
    """git은 진짜로 실행하고, 나머지는 등록된 처리기로 보낸다. 미등록 명령은 실패시킨다."""

    def __init__(self, root: Path):
        self.root = root
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []
        self.handlers: list[tuple] = []
        self.reviews = 0
        self.agent_prompts: list[str] = []
        # 기본 응답
        self.on(lambda c: c[:3] == ["gh", "pr", "list"], ok("[]"))
        self.on(lambda c: c[:3] == ["gh", "pr", "create"], ok(PR_URL + "\n"))
        self.on(lambda c: c[:3] == ["gh", "pr", "comment"], ok())
        self.on(lambda c: c[:3] == ["gh", "pr", "checks"], ok("all passed"))
        self.on(lambda c: c[0] == "osascript", ok())
        self.on(lambda c: "pytest" in c, ok("1 passed"))
        self.on(lambda c: c[:2] == ["npm", "test"], ok())
        self.on(lambda c: c[0] == "claude", self._review_clean)

    def on(self, match, result):
        self.handlers.append((match, result))

    def __call__(self, cmd, *, cwd, input=None, timeout=None):
        self.calls.append(list(cmd))
        self.inputs.append(input)
        if cmd[0] == "git":
            return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        for match, result in reversed(self.handlers):
            if match(cmd):
                return result(cmd, input) if callable(result) else result
        raise AssertionError(f"예상하지 못한 명령: {cmd}")

    # --- 에이전트 처리기 ---

    def _review_clean(self, cmd, prompt):
        return self.agent(review_rounds=[[]])(cmd, prompt)

    def agent(self, *, review_rounds, fix=None, ci_fix=None):
        """review_rounds[n] = n번째 리뷰가 남길 findings. 모자라면 마지막 것을 반복한다."""

        def handle(cmd, prompt):
            self.agent_prompts.append(prompt)
            if prompt.startswith("[ship:review]"):
                out = re.search(r"결과 파일: (\S+)", prompt).group(1)
                findings = review_rounds[min(self.reviews, len(review_rounds) - 1)]
                self.reviews += 1
                write(self.root, out, json.dumps({"findings": findings}, ensure_ascii=False))
            elif prompt.startswith("[ship:fix]"):
                (fix or append_impl)(self.root)
            elif prompt.startswith("[ship:ci-fix]"):
                (ci_fix or append_impl)(self.root)
            else:
                raise AssertionError(f"알 수 없는 에이전트 프롬프트: {prompt[:40]}")
            return ok('{"result": "done"}')

        return handle

    def count(self, pred) -> int:
        return sum(1 for c in self.calls if pred(c))

    def prompts(self, tag) -> list[str]:
        return [p for p in self.agent_prompts if p.startswith(tag)]


def append_impl(root: Path):
    p = root / "backend/openarchive/foo.py"
    p.write_text(p.read_text() + "Y = 3\n")


def add_skip(root: Path):
    p = root / "backend/tests/test_foo.py"
    p.write_text("import pytest\n\n@pytest.mark.skip\n" + p.read_text())


FIX = {"severity": "medium", "class": "fix", "file": "backend/openarchive/foo.py",
       "line": 1, "summary": "상수 이름", "reason": "규칙 위반"}
DECISION = {"severity": "high", "class": "decision", "file": "backend/openarchive/foo.py",
            "line": 1, "summary": "ADR과 충돌", "reason": "설계 재검토"}
FOLLOW_UP = {"severity": "low", "class": "follow-up", "file": "backend/openarchive/foo.py",
             "line": 1, "summary": "범위 밖 결함", "reason": "이 PR 범위 밖",
             "title": "foo 경계값 처리"}


def make(repo, runner, **kw) -> ship.Shipper:
    return ship.Shipper(PHASE, root=repo, runner=runner, sleep=lambda s: None, **kw)


# ---------------------------------------------------------------------------
# 순수 함수
# ---------------------------------------------------------------------------

class TestFindTampering:
    def test_deleted_test_file(self):
        v = ship.find_tampering("D\tbackend/tests/test_foo.py\nM\tbackend/openarchive/foo.py\n", "")
        assert v == ["테스트 파일 삭제: backend/tests/test_foo.py"]

    def test_test_renamed_to_non_test(self):
        v = ship.find_tampering("R100\tbackend/tests/test_foo.py\tbackend/tests/foo_old.py\n", "")
        assert v == ["테스트 파일 삭제: backend/tests/test_foo.py"]

    def test_added_skip_markers_in_tests(self):
        diff = (
            "+++ b/backend/tests/test_foo.py\n"
            "@@ -1 +1,2 @@\n"
            "+@pytest.mark.skip(reason='later')\n"
            "+@pytest.mark.xfail\n"
            "+    pytest.skip('x')\n"
            "+++ b/frontend/src/lib/a.test.ts\n"
            "+it.skip('x', () => {})\n"
            "+describe.skip('y', () => {})\n"
        )
        v = ship.find_tampering("", diff)
        assert len(v) == 5
        assert all(x.startswith("skip/xfail 추가: ") for x in v)

    def test_ignores_non_test_files_and_removed_lines(self):
        diff = (
            "+++ b/scripts/ship.py\n"
            "+PATTERN = re.compile(r'pytest.mark.skip')\n"
            "+++ b/backend/tests/test_foo.py\n"
            "-@pytest.mark.skip\n"
        )
        assert ship.find_tampering("M\tscripts/ship.py\n", diff) == []


class TestUntestedChanges:
    def test_impl_without_test(self):
        assert ship.untested_changes(["backend/openarchive/foo.py", "docs/ADR.md"]) == [
            "backend/openarchive/foo.py"
        ]

    def test_impl_with_test(self):
        assert ship.untested_changes(
            ["backend/openarchive/foo.py", "backend/tests/test_foo.py"]
        ) == []

    def test_docs_and_static_only(self):
        assert ship.untested_changes(
            ["docs/ADR.md", "backend/openarchive/static/index.html", f"phases/{PHASE}/index.json"]
        ) == []

    def test_frontend_and_migration_count_as_impl(self):
        assert ship.untested_changes(
            ["frontend/src/app/page.tsx", "backend/openarchive/migrations/034_x.sql"]
        ) == ["frontend/src/app/page.tsx", "backend/openarchive/migrations/034_x.sql"]


class TestNextStageAfterReview:
    def test_clean_goes_to_ci(self):
        assert ship.next_stage_after_review([], reviews=1, fixes=0) == "ci"

    def test_follow_up_only_goes_to_ci(self):
        assert ship.next_stage_after_review([FOLLOW_UP], reviews=1, fixes=0) == "ci"

    def test_fix_within_budget(self):
        assert ship.next_stage_after_review([FIX], reviews=2, fixes=1) == "fix"

    def test_decision_gates_even_with_fixes(self):
        with pytest.raises(ship.Gate, match="G2"):
            ship.next_stage_after_review([FIX, DECISION], reviews=1, fixes=0)

    def test_fix_after_budget_gates(self):
        with pytest.raises(ship.Gate, match="자동 수정 한도"):
            ship.next_stage_after_review([FIX], reviews=3, fixes=2)

    def test_budget_is_two_fixes_three_reviews(self):
        assert (ship.MAX_FIXES, ship.MAX_REVIEWS) == (2, 3)


# ---------------------------------------------------------------------------
# 통합 — 정상 경로
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_runs_to_ready_and_records_state(self, repo):
        r = FakeRunner(repo)
        assert make(repo, r).run() == ship.EXIT_OK

        st = ship.load_state(repo, PHASE)
        assert st["stage"] == "ready"
        assert st["issue"] == ISSUE
        assert st["branch"] == f"feat/{PHASE}"
        assert st["pr"] == 7
        assert st["last_commit"] == git(repo, "rev-parse", "HEAD")
        assert st["review_artifacts"] == [f"phases/{PHASE}/review-1.json"]
        assert st["gate"] is None

    def test_completed_phase_skips_execute(self, repo):
        r = FakeRunner(repo)
        make(repo, r).run()
        assert r.count(lambda c: any(a.endswith("execute.py") for a in c)) == 0

    def test_pr_body_closes_issue_outside_backticks(self, repo):
        r = FakeRunner(repo)
        make(repo, r).run()
        i = next(i for i, c in enumerate(r.calls) if c[:3] == ["gh", "pr", "create"])
        body = r.inputs[i]
        assert re.search(r"^Closes #42$", body, re.M)
        assert "`Closes" not in body
        assert "foo 상수 추가" in body  # step summary
        assert "backend/tests/test_foo.py" in body  # PR 전 검증에서 돌린 테스트
        assert "--title" in r.calls[i] and "feat: 데모 (#42)" in r.calls[i]

    def test_review_artifact_committed_and_pushed(self, repo):
        r = FakeRunner(repo)
        make(repo, r).run()
        assert git(repo, "status", "--porcelain") == ""
        assert f"phases/{PHASE}/review-1.json" in git(repo, "ls-files")
        assert git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", f"origin/feat/{PHASE}")

    def test_review_runs_in_separate_claude_process(self, repo):
        r = FakeRunner(repo)
        make(repo, r).run()
        review = next(c for c in r.calls if c[0] == "claude")
        assert review[:2] == ["claude", "-p"]
        prompt = r.prompts("[ship:review]")[0]
        assert "#42" in prompt and "origin/main" in prompt

    def test_ready_notifies(self, repo):
        r = FakeRunner(repo)
        make(repo, r).run()
        assert r.count(lambda c: c[0] == "osascript") == 1

    def test_rerun_at_ready_is_noop(self, repo):
        make(repo, FakeRunner(repo)).run()
        r2 = FakeRunner(repo)
        assert make(repo, r2).run() == ship.EXIT_OK
        assert r2.count(lambda c: c[0] in ("claude", "gh")) == 0


class TestExecuteStage:
    def test_pending_steps_run_execute_then_continue(self, repo):
        write_phase(repo, phase_index(status="pending"))
        commit_all(repo, "docs: 설계 되돌림")
        r = FakeRunner(repo)

        def run_execute(cmd, _):
            write_phase(repo, phase_index())
            commit_all(repo, "chore: 완료")
            return ok()

        r.on(lambda c: any(a.endswith("execute.py") for a in c), run_execute)
        assert make(repo, r).run() == ship.EXIT_OK
        exe = next(c for c in r.calls if any(a.endswith("execute.py") for a in c))
        assert exe[-1] == PHASE
        assert ship.load_state(repo, PHASE)["stage"] == "ready"

    def test_execute_failure_gates(self, repo):
        write_phase(repo, phase_index(status="pending"))
        commit_all(repo, "docs: 설계 되돌림")
        r = FakeRunner(repo)
        r.on(lambda c: any(a.endswith("execute.py") for a in c), ok(code=1))
        assert make(repo, r).run() == ship.EXIT_GATE
        st = ship.load_state(repo, PHASE)
        assert st["stage"] == "execute"
        assert "execute.py" in st["gate"]

    def test_uncommitted_design_gates_g1(self, repo):
        write_phase(repo, phase_index(status="pending"))
        r = FakeRunner(repo)
        assert make(repo, r).run() == ship.EXIT_GATE
        assert "G1" in ship.load_state(repo, PHASE)["gate"]
        assert r.count(lambda c: any(a.endswith("execute.py") for a in c)) == 0

    def test_missing_issue_config_gates(self, repo):
        idx = phase_index()
        del idx["issue"]
        write_phase(repo, idx)
        commit_all(repo, "docs: issue 빠짐")
        r = FakeRunner(repo)
        assert make(repo, r).run() == ship.EXIT_GATE
        assert "issue" in ship.load_state(repo, PHASE)["gate"]


# ---------------------------------------------------------------------------
# PR 전 검증
# ---------------------------------------------------------------------------

class TestVerify:
    def test_runs_changed_backend_tests(self, repo):
        r = FakeRunner(repo)
        make(repo, r).run()
        pyt = next(c for c in r.calls if "pytest" in c)
        assert pyt[-1] == "tests/test_foo.py"

    def test_failing_tests_gate_before_pr(self, repo):
        r = FakeRunner(repo)
        r.on(lambda c: "pytest" in c, ok("1 failed", code=1))
        assert make(repo, r).run() == ship.EXIT_GATE
        st = ship.load_state(repo, PHASE)
        assert st["stage"] == "verify" and "1 failed" in st["gate"]
        assert r.count(lambda c: c[:3] == ["gh", "pr", "create"]) == 0

    def test_dirty_tree_gates(self, repo):
        write(repo, "notes.txt", "scratch")
        r = FakeRunner(repo)
        assert make(repo, r).run() == ship.EXIT_GATE
        assert "working tree" in ship.load_state(repo, PHASE)["gate"]

    def test_impl_without_test_gates(self, repo):
        git(repo, "reset", "-q", "--hard", "HEAD~1")  # 구현 커밋을 버리고 테스트 없이 다시
        write(repo, "backend/openarchive/bar.py", "Z = 1\n")
        commit_all(repo, "feat(api): 테스트 없이")
        r = FakeRunner(repo)
        assert make(repo, r).run() == ship.EXIT_GATE
        assert "backend/openarchive/bar.py" in ship.load_state(repo, PHASE)["gate"]

    def test_incomplete_step_after_execute_gates(self, repo):
        idx = phase_index()
        idx["steps"].append({"step": 1, "name": "ui", "status": "error"})
        write_phase(repo, idx)
        commit_all(repo, "chore: step1 error")
        r = FakeRunner(repo)
        assert make(repo, r).run() == ship.EXIT_GATE
        assert "step 1" in ship.load_state(repo, PHASE)["gate"]

    def test_wrong_branch_gates(self, repo):
        git(repo, "checkout", "-q", "main")
        r = FakeRunner(repo)
        assert make(repo, r).run() == ship.EXIT_GATE
        assert f"feat/{PHASE}" in ship.load_state(repo, PHASE)["gate"]


# ---------------------------------------------------------------------------
# 리뷰·수정 루프
# ---------------------------------------------------------------------------

class TestReviewFixLoop:
    def test_fix_then_clean_review_proceeds(self, repo):
        r = FakeRunner(repo)
        r.on(lambda c: c[0] == "claude", r.agent(review_rounds=[[FIX], []]))
        assert make(repo, r).run() == ship.EXIT_OK
        st = ship.load_state(repo, PHASE)
        assert (st["reviews"], st["fixes"]) == (2, 1)
        assert r.count(lambda c: c[:3] == ["gh", "pr", "create"]) == 1  # 같은 PR
        log = git(repo, "log", "--format=%s", "origin/main..HEAD")
        assert f"fix: {PHASE} 리뷰 지적 반영 (1차)" in log
        fix_prompt = r.prompts("[ship:fix]")[0]
        assert "상수 이름" in fix_prompt

    def test_max_two_fixes_three_reviews_then_gate(self, repo):
        r = FakeRunner(repo)
        r.on(lambda c: c[0] == "claude", r.agent(review_rounds=[[FIX]]))
        assert make(repo, r).run() == ship.EXIT_GATE
        st = ship.load_state(repo, PHASE)
        assert (st["reviews"], st["fixes"]) == (3, 2)
        assert len(r.prompts("[ship:review]")) == 3
        assert len(r.prompts("[ship:fix]")) == 2
        assert "자동 수정 한도" in st["gate"]
        assert r.count(lambda c: c[:3] == ["gh", "pr", "checks"]) == 0

    def test_decision_gates_without_fixing(self, repo):
        r = FakeRunner(repo)
        r.on(lambda c: c[0] == "claude", r.agent(review_rounds=[[DECISION, FIX]]))
        assert make(repo, r).run() == ship.EXIT_GATE
        st = ship.load_state(repo, PHASE)
        assert "G2" in st["gate"] and st["stage"] == "review"
        assert r.prompts("[ship:fix]") == []

    def test_follow_up_is_candidate_not_issue(self, repo, capsys):
        r = FakeRunner(repo)
        r.on(lambda c: c[0] == "claude", r.agent(review_rounds=[[FOLLOW_UP]]))
        assert make(repo, r).run() == ship.EXIT_OK
        assert r.count(lambda c: c[:3] == ["gh", "issue", "create"]) == 0
        out = capsys.readouterr().out
        assert "foo 경계값 처리" in out and "이 PR 범위 밖" in out and "low" in out
        saved = json.loads((repo / f"phases/{PHASE}/review-1.json").read_text())
        assert saved["findings"][0]["title"] == "foo 경계값 처리"

    def test_review_comment_posted(self, repo):
        r = FakeRunner(repo)
        r.on(lambda c: c[0] == "claude", r.agent(review_rounds=[[FIX], []]))
        make(repo, r).run()
        assert r.count(lambda c: c[:3] == ["gh", "pr", "comment"]) == 2

    def test_fix_tamper_gates_and_is_not_committed(self, repo):
        r = FakeRunner(repo)
        r.on(lambda c: c[0] == "claude", r.agent(review_rounds=[[FIX]], fix=add_skip))
        head = git(repo, "rev-parse", "HEAD")
        assert make(repo, r).run() == ship.EXIT_GATE
        st = ship.load_state(repo, PHASE)
        assert "변조" in st["gate"] and "skip/xfail" in st["gate"]
        assert git(repo, "log", "-1", "--format=%s") == f"chore: {PHASE} 리뷰 1차 결과"
        assert git(repo, "rev-parse", "HEAD~1") == head
        assert "test_foo.py" in git(repo, "status", "--porcelain")  # 검토용으로 남겨 둔다

    def test_fix_without_changes_gates(self, repo):
        r = FakeRunner(repo)
        r.on(lambda c: c[0] == "claude", r.agent(review_rounds=[[FIX]], fix=lambda root: None))
        assert make(repo, r).run() == ship.EXIT_GATE
        assert "바꾸지 않았다" in ship.load_state(repo, PHASE)["gate"]

    def test_reviewer_touching_other_files_gates(self, repo):
        r = FakeRunner(repo)
        inner = r.agent(review_rounds=[[]])

        def sneaky(cmd, prompt):
            append_impl(repo)
            return inner(cmd, prompt)

        r.on(lambda c: c[0] == "claude", sneaky)
        assert make(repo, r).run() == ship.EXIT_GATE
        assert "리뷰어" in ship.load_state(repo, PHASE)["gate"]

    def test_invalid_review_output_gates(self, repo):
        r = FakeRunner(repo)

        def broken(cmd, prompt):
            out = re.search(r"결과 파일: (\S+)", prompt).group(1)
            write(repo, out, json.dumps({"findings": [{"class": "maybe"}]}))
            return ok()

        r.on(lambda c: c[0] == "claude", broken)
        assert make(repo, r).run() == ship.EXIT_GATE
        assert "리뷰 결과" in ship.load_state(repo, PHASE)["gate"]


# ---------------------------------------------------------------------------
# CI
# ---------------------------------------------------------------------------

def checks_sequence(*codes):
    seq = list(codes)

    def handle(cmd, _):
        code = seq.pop(0) if len(seq) > 1 else seq[0]
        return ok("checks", code=code)

    return handle


class TestCI:
    def _with_logs(self, r):
        r.on(lambda c: c[:3] == ["gh", "run", "list"], ok('[{"databaseId": 5}]'))
        r.on(lambda c: c[:3] == ["gh", "run", "view"], ok("FAILED test_foo.py::test_x - boom"))

    def test_ci_failure_auto_fixed_once(self, repo, capsys):
        r = FakeRunner(repo)
        self._with_logs(r)
        r.on(lambda c: c[:3] == ["gh", "pr", "checks"], checks_sequence(1, 0))
        assert make(repo, r).run() == ship.EXIT_OK
        st = ship.load_state(repo, PHASE)
        assert st["ci_fixes"] == 1 and len(st["ci_fix_commits"]) == 1
        assert "boom" in r.prompts("[ship:ci-fix]")[0]
        assert f"fix: {PHASE} CI 실패 수정" in git(repo, "log", "--format=%s", "-3")
        assert "리뷰 뒤 CI 수정 커밋" in capsys.readouterr().out

    def test_ci_failing_twice_gates(self, repo):
        r = FakeRunner(repo)
        self._with_logs(r)
        r.on(lambda c: c[:3] == ["gh", "pr", "checks"], checks_sequence(1))
        assert make(repo, r).run() == ship.EXIT_GATE
        st = ship.load_state(repo, PHASE)
        assert st["stage"] == "ci" and st["ci_fixes"] == 1
        assert len(r.prompts("[ship:ci-fix]")) == 1

    def test_ci_fix_tamper_gates(self, repo):
        r = FakeRunner(repo)
        self._with_logs(r)
        r.on(lambda c: c[:3] == ["gh", "pr", "checks"], checks_sequence(1, 0))
        r.on(lambda c: c[0] == "claude", r.agent(review_rounds=[[]], ci_fix=add_skip))
        assert make(repo, r).run() == ship.EXIT_GATE
        assert "변조" in ship.load_state(repo, PHASE)["gate"]

    def test_waits_until_checks_reported(self, repo):
        r = FakeRunner(repo)
        seq = [ok(stderr="no checks reported on the 'feat/x' branch", code=1), ok("passed")]
        r.on(lambda c: c[:3] == ["gh", "pr", "checks"], lambda c, _: seq.pop(0) if len(seq) > 1 else seq[0])
        assert make(repo, r).run() == ship.EXIT_OK
        assert r.count(lambda c: c[:3] == ["gh", "pr", "checks"]) == 2


# ---------------------------------------------------------------------------
# 재개·관문
# ---------------------------------------------------------------------------

class TestResumeAndGates:
    def test_resume_after_g2_with_from_review(self, repo):
        r = FakeRunner(repo)
        r.on(lambda c: c[0] == "claude", r.agent(review_rounds=[[DECISION]]))
        assert make(repo, r).run() == ship.EXIT_GATE

        # 사람이 결정을 반영해 커밋한 뒤 리뷰부터 다시
        append_impl(repo)
        commit_all(repo, "fix(api): 결정 반영")
        r2 = FakeRunner(repo)
        assert make(repo, r2).run(from_stage="review") == ship.EXIT_OK
        st = ship.load_state(repo, PHASE)
        assert st["pr"] == 7 and st["stage"] == "ready"
        assert r2.count(lambda c: c[:3] == ["gh", "pr", "create"]) == 0
        assert st["reviews"] == 1  # --from review는 횟수를 초기화한다
        assert st["review_artifacts"][-1] == f"phases/{PHASE}/review-2.json"

    def test_resume_continues_from_saved_stage(self, repo):
        r = FakeRunner(repo)
        r.on(lambda c: c[:3] == ["gh", "pr", "checks"], ok("boom", code=1))
        r.on(lambda c: c[:3] == ["gh", "run", "list"], ok('[{"databaseId": 5}]'))
        r.on(lambda c: c[:3] == ["gh", "run", "view"], ok("log"))
        assert make(repo, r).run() == ship.EXIT_GATE

        r2 = FakeRunner(repo)
        assert make(repo, r2).run() == ship.EXIT_OK
        assert r2.count(lambda c: c[0] == "claude") == 0  # 리뷰를 다시 하지 않는다
        assert r2.count(lambda c: "pytest" in c) == 0  # 검증도 다시 하지 않는다

    def test_branch_mismatch_on_resume_gates(self, repo):
        make(repo, FakeRunner(repo)).run(from_stage=None)
        st = ship.load_state(repo, PHASE)
        st["stage"] = "ci"
        ship.save_state(repo, PHASE, st)
        git(repo, "checkout", "-q", "main")
        assert make(repo, FakeRunner(repo)).run() == ship.EXIT_GATE

    def test_state_lives_outside_working_tree(self, repo):
        make(repo, FakeRunner(repo)).run()
        assert ship.state_path(repo, PHASE).is_relative_to(repo / ".git")

    def test_needs_vm_stops_at_g3(self, repo, capsys):
        write_phase(repo, phase_index(needs_vm=True))
        commit_all(repo, "docs: VM 실측 필요")
        assert make(repo, FakeRunner(repo)).run() == ship.EXIT_OK
        assert "G3" in capsys.readouterr().out


class TestMerge:
    def _ready(self, repo):
        make(repo, FakeRunner(repo)).run()

    def test_merge_requires_ready(self, repo):
        r = FakeRunner(repo)
        assert make(repo, r).merge() == ship.EXIT_GATE
        assert r.count(lambda c: c[:3] == ["gh", "pr", "merge"]) == 0

    def test_squash_merge_and_issue_closed(self, repo):
        self._ready(repo)
        r = FakeRunner(repo)
        r.on(lambda c: c[:3] == ["gh", "pr", "merge"], ok())
        r.on(lambda c: c[:3] == ["gh", "issue", "view"], ok("CLOSED\n"))
        assert make(repo, r).merge() == ship.EXIT_OK
        m = next(c for c in r.calls if c[:3] == ["gh", "pr", "merge"])
        assert "7" in m and "--squash" in m
        assert ship.load_state(repo, PHASE)["stage"] == "merged"

    def test_merge_warns_when_issue_still_open(self, repo, capsys):
        self._ready(repo)
        r = FakeRunner(repo)
        r.on(lambda c: c[:3] == ["gh", "pr", "merge"], ok())
        r.on(lambda c: c[:3] == ["gh", "issue", "view"], ok("OPEN\n"))
        assert make(repo, r).merge() == ship.EXIT_OK
        assert "#42" in capsys.readouterr().out

    def test_merge_refuses_when_checks_not_green(self, repo):
        self._ready(repo)
        r = FakeRunner(repo)
        r.on(lambda c: c[:3] == ["gh", "pr", "checks"], ok(code=1))
        assert make(repo, r).merge() == ship.EXIT_GATE
        assert r.count(lambda c: c[:3] == ["gh", "pr", "merge"]) == 0
