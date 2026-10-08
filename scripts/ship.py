#!/usr/bin/env python3
"""
Ship Pipeline — 하네스 phase를 머지 대기까지 잇는 오케스트레이션 레이어.

execute.py(step 실행)는 그대로 두고 그 바깥에서 단계를 넘긴다:

    execute → verify → pr → review ⇄ fix → ci → ready   (--merge → merged)

사람의 관문에서만 멈춘다 — G1 설계 승인(phase가 커밋돼 있어야 시작), G2 decision 지적,
G3 VM 실측(phase index의 needs_vm), G4 머지(--merge). 그 밖에 기계가 넘으면 안 되는 실패
(변조 검사·자동 수정 한도·CI 재실패·테스트 실패)도 멈춘다.

상태는 .git/ship/<phase>.json에 남는다 — 작업 트리 밖이라 자동 수정의 `git add -A`에
휩쓸리지 않는다. 다음 실행은 멈춘 단계에서 재개한다.

Usage:
    python3 scripts/ship.py <phase-dir>                # 진행 (멈춘 곳부터 재개)
    python3 scripts/ship.py <phase-dir> --from review  # 그 단계부터 다시 (리뷰·수정 횟수 초기화)
    python3 scripts/ship.py <phase-dir> --merge        # G4: squash 머지 + 이슈 닫힘 확인
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 리뷰·수정 세션 모델. 구현은 execute.py가 Codex 우선으로 돌리므로 리뷰는 다른 모델이 된다.
MODEL = "opus"
AGENT_TIMEOUT = 1800
CI_TIMEOUT = 3600

STAGES = ("execute", "verify", "pr", "review", "fix", "ci", "ready", "merged")
MAX_FIXES = 2
# 두 번째 수정 뒤에도 독립 리뷰로 지적이 없는지 확인한다 — 리뷰는 수정보다 한 번 많다.
MAX_REVIEWS = MAX_FIXES + 1
# CI 자동 수정은 리뷰 예산과 별개다. 재리뷰를 받지 않으므로 ready 요약에 따로 드러낸다.
MAX_CI_FIXES = 1
CI_WAIT_TRIES = 20
CI_WAIT_SECONDS = 15

EXIT_OK, EXIT_GATE = 0, 3
CLASSES = ("fix", "decision", "follow-up")
SEVERITIES = ("high", "medium", "low")
TZ = timezone(timedelta(hours=9))

TEST_PATH = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.py$|\.test\.[jt]sx?$")
IMPL_PATH = re.compile(
    r"^backend/openarchive/(?!static/).*\.(py|sql)$"
    r"|^frontend/src/.*\.[jt]sx?$"
    r"|^scripts/[^/]+\.py$"
)
# 테스트 정의 줄 — 지워진 이름이 다시 추가되지 않으면 테스트 삭제로 본다.
TEST_DEF = re.compile(
    r"^\s*(?:async\s+)?def\s+(test_\w+)"
    r"|^\s*(?:it|test)\(\s*(['\"`])(.+?)\2"
)
# 에이전트 세션은 권한 확인 없이 돈다 — 이슈 생성·머지·커밋·push는 도구 수준에서 막는다.
# 커밋·push는 변조 검사를 거쳐 하네스만 한다. `--disallowedTools`는 권한 우회 모드에서도 거부된다(실측).
AGENT_DENIED = ("Bash(gh issue create:*) Bash(gh api:*) Bash(gh pr merge:*) "
                "Bash(git commit:*) Bash(git push:*)")
SKIP_MARKER = re.compile(
    r"pytest\.mark\.(skip|xfail)|pytest\.(skip|xfail)\("
    r"|\b(it|test|describe)\.(skip|todo)\(|\bx(it|describe)\("
)


class Gate(Exception):
    """사람의 판단이 필요해 멈춘다. 상태는 저장되고 다음 실행이 같은 단계에서 재개한다."""


def run_cmd(cmd, *, cwd, input=None, timeout=None, stream=False):
    """기본 실행기. stream이면 출력을 터미널로 흘린다(execute.py의 긴 진행 표시)."""
    try:
        return subprocess.run(cmd, cwd=cwd, input=input, text=True, timeout=timeout,
                              capture_output=not stream, check=False)
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"{timeout}초 동안 끝나지 않아 중단함")


def tail(text: str | None, n: int = 40) -> str:
    return "\n".join((text or "").strip().splitlines()[-n:])


# ---------------------------------------------------------------------------
# 판정 함수
# ---------------------------------------------------------------------------

def is_test(path: str) -> bool:
    return bool(TEST_PATH.search(path))


def untested_changes(paths: list[str]) -> list[str]:
    """구현 파일이 바뀌었는데 테스트 파일이 하나도 안 바뀌었으면 그 구현 파일들."""
    if any(is_test(p) for p in paths):
        return []
    return [p for p in paths if IMPL_PATH.search(p)]


def find_tampering(name_status: str, diff: str) -> list[str]:
    """자동 수정이 테스트(파일·함수)를 지우거나 skip/xfail을 붙였는지. 단언 약화는 잡지 못한다."""
    found = []
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        kind = parts[0][:1]
        # 이름 바꾸기로 테스트 경로를 벗어나는 것도 삭제로 본다.
        renamed_away = kind == "R" and len(parts) > 2 and not is_test(parts[2])
        if (kind == "D" or renamed_away) and is_test(parts[1]):
            found.append(f"테스트 파일 삭제: {parts[1]}")

    current = None
    removed: dict[str, list[str]] = {}
    added: dict[str, set[str]] = {}
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            continue
        if not current or not is_test(current) or line.startswith("--- "):
            continue
        if line.startswith("+") and SKIP_MARKER.search(line):
            found.append(f"skip/xfail 추가: {current}: {line[1:].strip()}")
        m = TEST_DEF.search(line[1:]) if line[:1] in "+-" else None
        if m:
            name = m.group(1) or m.group(3)
            if line.startswith("-"):
                removed.setdefault(current, []).append(name)
            else:
                added.setdefault(current, set()).add(name)
    for path, names in removed.items():
        found += [f"테스트 함수 삭제: {path}: {n}" for n in names if n not in added.get(path, set())]
    return found


def next_stage_after_review(findings: list[dict], reviews: int, fixes: int) -> str:
    if any(f["class"] == "decision" for f in findings):
        raise Gate("G2: decision 지적 — 설계 판단이 필요하다. 반영·커밋한 뒤 --from review로 재개")
    if not any(f["class"] == "fix" for f in findings):
        return "ci"
    if fixes >= MAX_FIXES or reviews >= MAX_REVIEWS:
        raise Gate(f"자동 수정 한도({MAX_FIXES}회) 뒤에도 fix 지적이 남았다 — 마지막 리뷰 결과를 "
                   "직접 반영·커밋한 뒤 --from review로 재개")
    return "fix"


# ---------------------------------------------------------------------------
# 상태 파일
# ---------------------------------------------------------------------------

def state_path(root, phase: str) -> Path:
    git_dir = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=root,
                             capture_output=True, text=True, check=False).stdout.strip()
    return Path(root) / git_dir / "ship" / f"{phase}.json"


def load_state(root, phase: str) -> dict | None:
    p = state_path(root, phase)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save_state(root, phase: str, state: dict):
    p = state_path(root, phase)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# 파이프라인
# ---------------------------------------------------------------------------

class Shipper:
    def __init__(self, phase_dir: str, *, root=ROOT, runner=run_cmd, sleep=time.sleep):
        self.phase_dir = phase_dir
        self.phase_rel = f"phases/{phase_dir}"
        self.root = Path(root)
        self._runner = runner
        self._sleep = sleep
        self.state: dict = {}

    # --- 진입점 ---

    def run(self, from_stage: str | None = None) -> int:
        self.state = load_state(self.root, self.phase_dir) or self._blank_state()
        if self.state["stage"] == "merged":
            print(f"  {self.phase_dir}는 이미 머지됐다 (PR #{self.state['pr']})")
            return EXIT_OK
        try:
            self._prepare(from_stage)
            while self.state["stage"] != "ready":
                print(f"\n▶ {self.state['stage']}")
                getattr(self, f"_stage_{self.state['stage']}")()
                self._save()
        except Gate as g:
            return self._stop(str(g))
        self._save()
        self._report_ready()
        return EXIT_OK

    def merge(self, vm_verified: bool = False) -> int:
        """G4 — 사람이 확인한 뒤 부른다. needs_vm phase는 G3 실측 확인(vm_verified)도 요구한다."""
        self.state = load_state(self.root, self.phase_dir) or {}
        if self.state.get("stage") != "ready":
            print(f"  머지할 수 없다 — ready 단계가 아니다 (현재: {self.state.get('stage', '시작 전')})")
            return EXIT_GATE
        st = self.state
        try:
            self._prepare(None)
            if st["needs_vm"] and not vm_verified:
                raise Gate("G3: VM 실측이 필요한 phase다 — 실측을 마친 뒤 --merge --vm-verified")
            if self._sh("gh", "pr", "checks", str(st["pr"])).returncode != 0:
                raise Gate(f"CI가 초록이 아니다 — 머지하지 않았다: {st['pr_url']}")
            reviewed = self._require_reviewed_head()
            # 확인과 머지 사이에 push가 끼어들어도 GitHub가 head 불일치로 거부한다.
            r = self._sh("gh", "pr", "merge", str(st["pr"]), "--squash",
                         "--match-head-commit", reviewed)
            if r.returncode != 0:
                raise Gate(f"gh pr merge 실패: {tail(r.stderr)}")
        except Gate as g:
            return self._stop(str(g))
        st["stage"] = "merged"
        self._save()
        print(f"  ✓ PR #{st['pr']} squash 머지")

        # 백틱 안의 Closes는 이슈를 닫지 않는다 — 머지 성공만 보고 넘어가지 않는다.
        self._sleep(5)
        r = self._sh("gh", "issue", "view", str(st["issue"]), "--json", "state", "-q", ".state")
        if r.stdout.strip() == "CLOSED":
            print(f"  ✓ 이슈 #{st['issue']} 닫힘")
        else:
            print(f"  ⚠ 이슈 #{st['issue']}가 아직 열려 있다 — PR 본문의 Closes를 확인하고 직접 닫을 것")
        self._sh("git", "fetch", "-q", "origin")
        return EXIT_OK

    def _require_reviewed_head(self) -> str:
        """머지할 PR head가 마지막 독립 리뷰가 본 커밋인지 확인한다 — 실무의 「새 커밋이 오면 승인 무효」.

        1인 저장소에서는 본인 PR을 승인할 수 없어 GitHub 브랜치 보호로 강제할 수 없으므로 여기서 막는다.
        """
        st = self.state
        reviewed = st.get("reviewed_head")
        if not reviewed:
            raise Gate("리뷰 기록(reviewed_head)이 없다 — --from review로 리뷰를 받은 뒤 머지할 것")
        r = self._sh("gh", "pr", "view", str(st["pr"]), "--json", "headRefOid", "-q", ".headRefOid")
        head = r.stdout.strip()
        if r.returncode != 0 or not head:
            raise Gate(f"PR head를 읽지 못했다 — 머지하지 않았다: {tail(r.stderr)}")
        if head != reviewed:
            count = self._sh("git", "rev-list", "--count", f"{reviewed}..{head}").stdout.strip() or "?"
            raise Gate(f"리뷰 뒤 커밋 {count}개가 PR에 있다(리뷰 {reviewed[:7]} → head {head[:7]}) — "
                       "--from review로 다시 리뷰받은 뒤 머지할 것")
        return reviewed

    # --- 준비·저장·멈춤 ---

    def _blank_state(self) -> dict:
        return {
            "phase": self.phase_dir, "stage": "execute", "issue": None, "branch": None,
            "needs_vm": False, "pr": None, "pr_url": None,
            "reviews": 0, "fixes": 0, "ci_fixes": 0,
            "review_artifacts": [], "ci_fix_commits": [], "tests_run": [],
            "last_commit": None, "reviewed_head": None, "gate": None, "updated_at": None,
        }

    def _prepare(self, from_stage: str | None):
        st = self.state
        st["gate"] = None
        idx = self._index()
        for key in ("issue", "title"):
            if not idx.get(key):
                raise Gate(f"G1: {self.phase_rel}/index.json에 {key}가 없다 — 설계에 넣고 커밋할 것")
        st["issue"] = idx["issue"]
        st["branch"] = f"feat/{idx.get('phase', self.phase_dir)}"
        st["needs_vm"] = bool(idx.get("needs_vm"))

        current = self._current_branch()
        if current != st["branch"]:
            raise Gate(f"현재 브랜치가 {current}다 — {st['branch']}에서 실행할 것")
        head = self._git("rev-parse", "HEAD")
        if st["last_commit"] and head != st["last_commit"]:
            print(f"  참고: 마지막 기록({st['last_commit'][:7]}) 뒤에 커밋이 있다 — HEAD {head[:7]}")

        if from_stage:
            st["stage"] = from_stage
            if STAGES.index(from_stage) <= STAGES.index("review"):
                st["reviews"] = st["fixes"] = 0
            if STAGES.index(from_stage) <= STAGES.index("ci"):
                st["ci_fixes"] = 0

    def _save(self):
        self.state["updated_at"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S%z")
        r = self._sh("git", "rev-parse", "HEAD")
        if r.returncode == 0:
            self.state["last_commit"] = r.stdout.strip()
        save_state(self.root, self.phase_dir, self.state)

    def _stop(self, reason: str) -> int:
        self.state["gate"] = reason
        self._save()
        print(f"\n⏸ 멈춤 [{self.state['stage']}] {reason}")
        print(f"  해결한 뒤 다시 실행: python3 scripts/ship.py {self.phase_dir}")
        self._notify(f"멈춤({self.state['stage']}): {reason.splitlines()[0][:80]}")
        return EXIT_GATE

    # --- 단계 ---

    def _stage_execute(self):
        if self._git("status", "--porcelain", "--", self.phase_rel):
            raise Gate(f"G1: {self.phase_rel}에 커밋되지 않은 변경이 있다 — 승인한 설계를 커밋한 뒤 실행할 것")
        steps = self._index()["steps"]
        for s in steps:
            if s.get("status") in ("error", "blocked"):
                why = s.get("error_message") or s.get("blocked_reason") or ""
                raise Gate(f"step {s['step']} ({s['name']}) {s['status']}: {why} — "
                           "해결 후 status를 pending으로 되돌릴 것")
        if any(s.get("status") == "pending" for s in steps):
            r = self._sh(sys.executable, "scripts/execute.py", self.phase_dir, stream=True)
            if r.returncode != 0:
                raise Gate(f"execute.py 종료 코드 {r.returncode} — "
                           f"{self.phase_rel}/index.json의 error/blocked step을 확인할 것")
        self.state["stage"] = "verify"

    def _stage_verify(self):
        """PR 전 하네스 산출물 검증 — step 완료·작업 트리·테스트 추가·테스트 재실행."""
        self._git("fetch", "-q", "origin")
        for s in self._index()["steps"]:
            if s.get("status") != "completed":
                raise Gate(f"step {s['step']} ({s['name']})가 completed가 아니다: {s.get('status')}")
        self._require_clean()
        if self._git("rev-list", "--count", "origin/main..HEAD") == "0":
            raise Gate("origin/main 대비 커밋이 없다")
        changed = self._git("diff", "--name-only", "origin/main...HEAD").splitlines()
        missing = untested_changes(changed)
        if missing:
            raise Gate("구현이 바뀌었는데 테스트 변경이 없다: " + ", ".join(missing))
        self.state["tests_run"] = self._run_changed_tests(changed)
        self.state["stage"] = "pr"

    def _stage_pr(self):
        st = self.state
        idx = self._index()
        self._git("push", "-q", "-u", "origin", st["branch"])
        if not st["pr"]:
            r = self._sh("gh", "pr", "list", "--head", st["branch"], "--state", "open",
                         "--json", "number,url")
            existing = json.loads(r.stdout or "[]") if r.returncode == 0 else []
            if existing:
                st["pr"], st["pr_url"] = existing[0]["number"], existing[0]["url"]
            else:
                r = self._sh("gh", "pr", "create", "--base", "main", "--head", st["branch"],
                             "--title", idx["title"], "--body-file", "-", input=self._pr_body(idx))
                if r.returncode != 0:
                    raise Gate(f"gh pr create 실패: {tail(r.stderr)}")
                st["pr_url"] = r.stdout.strip().splitlines()[-1]
                st["pr"] = int(st["pr_url"].rstrip("/").rsplit("/", 1)[-1])
            print(f"  PR #{st['pr']} {st['pr_url']}")
        st["stage"] = "review"

    def _stage_review(self):
        st = self.state
        if st["reviews"] >= MAX_REVIEWS:
            raise Gate(f"리뷰 한도({MAX_REVIEWS}회)에 닿았다 — 마지막 리뷰 결과를 직접 반영·커밋한 뒤 "
                       "--from review로 재개")
        self._require_clean()
        n = len(st["review_artifacts"]) + 1
        rel = f"{self.phase_rel}/review-{n}.json"
        self._agent(self._review_prompt(rel))

        others = [line for line in self._git("status", "--porcelain", "--untracked-files=all").splitlines()
                  if not line.endswith(rel)]
        if others:
            raise Gate("리뷰어가 리뷰 결과 밖의 파일을 바꿨다 — 검토 후 되돌릴 것:\n" + "\n".join(others))
        findings = self._load_findings(rel)

        self._git("add", rel)
        self._git("commit", "-q", "-m", f"chore: {self.phase_dir} 리뷰 {n}차 결과")
        self._git("push", "-q", "origin", st["branch"])
        # 리뷰 결과 커밋까지가 이 리뷰가 본 판이다 — 이후 커밋은 다시 리뷰받아야 머지된다.
        st["reviewed_head"] = self._git("rev-parse", "HEAD")
        st["reviews"] += 1
        st["review_artifacts"].append(rel)
        self._sh("gh", "pr", "comment", str(st["pr"]), "--body-file", "-",
                 input=self._review_comment(n, findings))
        counts = {c: sum(1 for f in findings if f["class"] == c) for c in CLASSES}
        print(f"  리뷰 {n}차: " + " · ".join(f"{c} {k}" for c, k in counts.items()))
        st["stage"] = next_stage_after_review(findings, st["reviews"], st["fixes"])

    def _stage_fix(self):
        st = self.state
        self._require_clean()
        if not st["review_artifacts"]:
            raise Gate("수정할 리뷰 결과가 없다 — --from review로 재개")
        fixes = [f for f in self._load_findings(st["review_artifacts"][-1]) if f["class"] == "fix"]
        n = st["fixes"] + 1
        self._agent(self._fix_prompt(
            "[ship:fix]", "리뷰 지적(class=fix)",
            json.dumps(fixes, ensure_ascii=False, indent=2)))
        self._commit_auto_fix(f"fix: {self.phase_dir} 리뷰 지적 반영 ({n}차)")
        st["fixes"] = n
        st["stage"] = "review"

    def _stage_ci(self):
        st = self.state
        r = self._wait_checks()
        if r.returncode == 0:
            st["stage"] = "ready"
            return
        if r.returncode == 124:
            # 실패가 아니라 대기 초과다 — 로그 없는 자동 수정으로 CI 수정 예산을 쓰지 않는다.
            raise Gate(f"CI 대기 시간({CI_TIMEOUT}초)을 넘겼다 — Actions 상태를 확인한 뒤 재개: {st['pr_url']}")
        if st["ci_fixes"] >= MAX_CI_FIXES:
            raise Gate(f"CI 실패 — 자동 수정 {MAX_CI_FIXES}회 뒤에도 실패: {st['pr_url']}")
        self._require_clean()
        self._agent(self._fix_prompt("[ship:ci-fix]", "CI 실패 로그", self._failed_log()))
        sha = self._commit_auto_fix(f"fix: {self.phase_dir} CI 실패 수정")
        st["ci_fixes"] += 1
        st["ci_fix_commits"].append(sha)
        # 리뷰 뒤 커밋이므로 바뀐 부분을 다시 리뷰받는다. 리뷰 한도면 review 단계가 사람에게 넘긴다.
        st["stage"] = "review"

    # --- 단계 도우미 ---

    def _run_changed_tests(self, changed: list[str]) -> list[str]:
        tests = [p for p in changed if is_test(p) and (self.root / p).is_file()]
        backend = [p for p in tests if re.match(r"backend/tests/(.*/)?test_[^/]*\.py$", p)]
        scripts = [p for p in tests if re.match(r"scripts/test_[^/]*\.py$", p)]
        front = [p for p in tests if re.match(r"frontend/.*\.test\.[jt]sx?$", p)]
        # 셸 기본 python에는 pytest가 없다 — check.sh와 같이 backend/.venv를 직접 쓴다.
        py = str(self.root / "backend/.venv/bin/python")
        runs = []
        if backend:
            runs.append(([py, "-m", "pytest", "-q", *[p.removeprefix("backend/") for p in backend]],
                         self.root / "backend"))
        if scripts:
            runs.append(([py, "-m", "pytest", "-q", *scripts], self.root))
        if front:
            runs.append((["npm", "test", "--", *[p.removeprefix("frontend/") for p in front]],
                         self.root / "frontend"))
        for cmd, cwd in runs:
            print(f"  테스트: {' '.join(cmd[-3:])}")
            r = self._sh(*cmd, cwd=cwd, timeout=AGENT_TIMEOUT)
            if r.returncode != 0:
                raise Gate(f"PR 전 테스트 실패: {' '.join(cmd)}\n{tail((r.stdout or '') + (r.stderr or ''))}")
        return backend + scripts + front

    def _commit_auto_fix(self, msg: str) -> str:
        """모든 자동 수정의 공통 출구 — 변조 검사를 통과해야 커밋·push한다."""
        self._git("add", "-A")
        names = self._git("diff", "--cached", "--name-status", "-M")
        if not names:
            raise Gate("자동 수정 에이전트가 아무것도 바꾸지 않았다 — 지적을 직접 확인할 것")
        bad = find_tampering(names, self._git("diff", "--cached", "-U0", "-M"))
        if bad:
            self._git("reset", "-q")
            raise Gate("자동 수정이 테스트를 변조했다 — 커밋하지 않고 working tree에 남겨 둠:\n"
                       + "\n".join(bad))
        self._git("commit", "-q", "-m", msg)
        self._git("push", "-q", "origin", self.state["branch"])
        print(f"  커밋: {msg}")
        return self._git("rev-parse", "HEAD")

    def _wait_checks(self) -> subprocess.CompletedProcess:
        # push 직후의 새 커밋에는 체크가 아직 없다 — 보고될 때까지 기다린다.
        for _ in range(CI_WAIT_TRIES):
            r = self._sh("gh", "pr", "checks", str(self.state["pr"]), "--watch", "--interval", "30",
                         timeout=CI_TIMEOUT)
            if "no checks reported" not in (r.stdout or "") + (r.stderr or ""):
                return r
            self._sleep(CI_WAIT_SECONDS)
        raise Gate("CI 체크가 보고되지 않는다 — Actions 상태를 확인할 것")

    def _failed_log(self) -> str:
        r = self._sh("gh", "run", "list", "--branch", self.state["branch"], "--limit", "1",
                     "--json", "databaseId")
        runs = json.loads(r.stdout or "[]") if r.returncode == 0 else []
        if not runs:
            return "(실패 로그를 가져오지 못했다 — gh pr checks로 확인할 것)"
        return tail(self._sh("gh", "run", "view", str(runs[0]["databaseId"]), "--log-failed").stdout, 300)

    def _load_findings(self, rel: str) -> list[dict]:
        try:
            findings = json.loads((self.root / rel).read_text(encoding="utf-8"))["findings"]
        except (OSError, ValueError, KeyError, TypeError):
            raise Gate(f"리뷰 결과 파일을 읽지 못했다: {rel}")
        if not isinstance(findings, list):
            raise Gate(f"리뷰 결과 형식이 틀렸다({rel}): findings가 배열이 아니다")
        for f in findings:
            ok = (isinstance(f, dict) and f.get("class") in CLASSES
                  and f.get("severity") in SEVERITIES and f.get("summary"))
            if ok and f["class"] == "follow-up":
                ok = bool(f.get("title") and f.get("reason"))
            if not ok:
                raise Gate(f"리뷰 결과 형식이 틀렸다({rel}): {f}")
        return findings

    def _issue_candidates(self) -> list[tuple[dict, str]]:
        seen, out = set(), []
        for rel in self.state["review_artifacts"]:
            for f in self._load_findings(rel):
                if f["class"] == "follow-up" and f["title"] not in seen:
                    seen.add(f["title"])
                    out.append((f, rel))
        return out

    def _report_ready(self):
        st = self.state
        print(f"\n{'=' * 60}\n  {self.phase_dir} — 머지 대기\n{'=' * 60}")
        print(f"  PR #{st['pr']} {st['pr_url']}")
        print(f"  리뷰 {st['reviews']}회 · 자동 수정 {st['fixes']}회 · 리뷰 결과 {', '.join(st['review_artifacts'])}")
        candidates = self._issue_candidates()
        if candidates:
            print("\n  이슈 후보 (등록은 사람이 판단):")
            for f, rel in candidates:
                print(f"  - [{f['severity']}] {f['title']} — {f['reason']} ({rel})")
        if st["ci_fix_commits"]:
            print("\n  CI 자동 수정 커밋 — 재리뷰를 거쳤다:")
            for sha in st["ci_fix_commits"]:
                print(f"  - {self._git('log', '-1', '--format=%h %s', sha)}")
        if st["needs_vm"]:
            print("\n  ⏸ G3: VM 실측이 필요한 phase다 — 실측을 마친 뒤 --merge --vm-verified")
        else:
            print(f"\n  ⏸ G4: 확인 후 python3 scripts/ship.py {self.phase_dir} --merge")
        self._notify(f"머지 대기: PR #{st['pr']}" + (" (VM 실측 필요)" if st["needs_vm"] else ""))

    # --- 프롬프트·본문 ---

    def _pr_body(self, idx: dict) -> str:
        st = self.state
        steps = idx["steps"]
        tests = ", ".join(f"`{t}`" for t in st["tests_run"]) or "없음 (테스트 변경 없음)"
        lines = [
            f"Closes #{idx['issue']}",
            "",
            f"하네스 phase `{self.phase_dir}` {len(steps)} step.",
            "",
            "## step",
            *[f"- step {s['step']} `{s['name']}`: {s.get('summary', '')}" for s in steps],
            "",
            "## PR 전 검증 (ship.py)",
            "- 모든 step completed · working tree 깨끗함 · 구현 변경에 테스트 변경 동반",
            f"- 다시 돌린 테스트: {tests}",
            "",
            "## Self-review 체크리스트",
            "- [ ] ADR 준수",
            "- [ ] CLAUDE.md CRITICAL 위반 없음",
            "- [ ] 전체 검증은 CI(`scripts/check.sh`)",
            "",
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
        ]
        return "\n".join(lines) + "\n"

    def _review_prompt(self, rel: str) -> str:
        st = self.state
        prev = ", ".join(st["review_artifacts"]) or "없음"
        since = ""
        reviewed = st.get("reviewed_head")
        if reviewed and reviewed != self._git("rev-parse", "HEAD"):
            since = (f"- 직전 리뷰 이후 변경: `git diff {reviewed}..HEAD` — 이번 리뷰는 이 변경을 중점으로 보되, "
                     "전체 diff의 맥락에서 판단하라\n")
        return f"""[ship:review]
당신은 이 저장소의 독립 리뷰어다. 구현에 참여하지 않았다. 코드를 고치지 마라 — 결과 파일 하나만 쓴다.

- 대상: `git diff origin/main...HEAD` (브랜치 {st['branch']}, PR #{st['pr']})
- 스펙: GitHub 이슈 #{st['issue']} (`gh issue view {st['issue']}`), 하네스 설계 `{self.phase_rel}/step*.md`
- 기준: CLAUDE.md(특히 CRITICAL), docs/ADR.md, docs/ARCHITECTURE.md
- 이전 리뷰: {prev} — 반영된 지적을 되풀이하지 말고, 반영이 맞는지 확인하라
{since}
두 축으로 본다:
1. Standards — 문서화된 규칙 위반. 규칙 출처를 reason에 적는다
2. Spec — 이슈·설계가 요구했는데 빠졌거나 틀린 것, 요구하지 않은 범위 확장

지적마다 class를 하나 붙인다:
- fix: 이 PR 안에서 고칠 수 있고 설계 판단이 필요 없다
- decision: 설계·ADR·스펙 해석을 바꿔야 하거나 선택지가 갈린다 — 사람이 정한다
- follow-up: 실재하는 문제지만 이 PR 범위 밖 — 이슈 후보로 남긴다(title 필수). 이슈를 직접 만들지 마라

확신이 없는 지적과 취향 문제는 넣지 마라. 문제가 없으면 findings를 빈 배열로 둔다.

결과 파일: {rel}
형식(JSON):
{{"findings": [{{"severity": "high|medium|low", "class": "fix|decision|follow-up", "file": "경로", "line": 1, "summary": "한 줄", "reason": "근거", "title": "follow-up일 때 이슈 제목"}}]}}
"""

    def _fix_prompt(self, tag: str, what: str, payload: str) -> str:
        st = self.state
        return f"""{tag}
당신은 이 저장소의 개발자다. 브랜치 {st['branch']}(PR #{st['pr']}, 이슈 #{st['issue']})에 아래 {what}을 반영하라.

{payload}

규칙:
- 지적된 것만 고쳐라. 범위를 넓히지 마라. CLAUDE.md 규칙을 따른다.
- 테스트를 지우거나 skip/xfail을 붙이거나 단언을 약하게 만들지 마라. 테스트가 틀렸다고 판단되면
  아무것도 바꾸지 말고 끝내라 — 하네스가 변조를 검사하고, 변경이 없으면 사람에게 넘긴다.
- 고친 범위의 테스트를 돌려 통과를 확인하라(backend: backend/.venv/bin/pytest, frontend: npm test).
  프론트엔드를 바꿨다면 frontend에서 npm run build:static으로 backend/openarchive/static을 갱신하라.
- 커밋·push하지 마라. 하네스가 변조 검사 뒤 커밋한다.
"""

    @staticmethod
    def _review_comment(n: int, findings: list[dict]) -> str:
        if not findings:
            return f"### ship.py 리뷰 {n}차\n\n지적 없음.\n"
        rows = [f"| {f['class']} | {f['severity']} | `{f.get('file', '')}:{f.get('line', '')}` | {f['summary']} |"
                for f in findings]
        return (f"### ship.py 리뷰 {n}차\n\n| class | severity | 위치 | 내용 |\n|---|---|---|---|\n"
                + "\n".join(rows) + "\n")

    # --- 실행 ---

    def _sh(self, *cmd, cwd=None, input=None, timeout=None, stream=False):
        return self._runner(list(cmd), cwd=cwd or self.root, input=input, timeout=timeout,
                            stream=stream)

    def _git(self, *args) -> str:
        r = self._sh("git", *args)
        if r.returncode != 0:
            raise Gate(f"git {' '.join(args)} 실패: {tail(r.stderr)}")
        return r.stdout.strip()

    def _agent(self, prompt: str):
        # 매번 새 프로세스 — 리뷰는 구현 맥락이 없는 새 세션에서 돈다.
        r = self._sh("claude", "-p", "--dangerously-skip-permissions", "--model", MODEL,
                     "--disallowedTools", AGENT_DENIED,
                     "--output-format", "json", input=prompt, timeout=AGENT_TIMEOUT)
        if r.returncode != 0:
            raise Gate(f"claude 세션 실패(code {r.returncode}): {tail(r.stderr or r.stdout)}")

    def _index(self) -> dict:
        p = self.root / self.phase_rel / "index.json"
        if not p.exists():
            raise Gate(f"{self.phase_rel}/index.json이 없다 — feat/{self.phase_dir} 브랜치에서 실행할 것 "
                       f"(현재 {self._current_branch()})")
        return json.loads(p.read_text(encoding="utf-8"))

    def _current_branch(self) -> str:
        return self._sh("git", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    def _require_clean(self):
        dirty = self._git("status", "--porcelain", "--untracked-files=all")
        if dirty:
            raise Gate(f"working tree가 깨끗하지 않다:\n{dirty}")

    def _notify(self, msg: str):
        text = msg.replace("\\", "").replace('"', "'")
        self._sh("osascript", "-e", f'display notification "{text}" with title "ship {self.phase_dir}"')


def main():
    parser = argparse.ArgumentParser(description="Ship Pipeline — phase를 머지 대기까지 잇는다")
    parser.add_argument("phase_dir", help="Phase directory name (e.g. m28-remote-mcp)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--from", dest="from_stage", choices=STAGES[:-2],
                       help="이 단계부터 다시 (review 이전이면 리뷰·수정 횟수 초기화)")
    group.add_argument("--merge", action="store_true", help="G4: squash 머지 + 이슈 닫힘 확인")
    parser.add_argument("--vm-verified", action="store_true",
                        help="G3: needs_vm phase의 VM 실측을 마쳤다 (--merge와 함께)")
    args = parser.parse_args()
    if args.vm_verified and not args.merge:
        parser.error("--vm-verified는 --merge와 함께 쓴다")

    shipper = Shipper(args.phase_dir)
    if args.merge:
        sys.exit(shipper.merge(vm_verified=args.vm_verified))
    sys.exit(shipper.run(from_stage=args.from_stage))


if __name__ == "__main__":
    main()
