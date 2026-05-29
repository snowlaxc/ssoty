# ssoty

**Static cross-harness rule coherence auditor for AI coding agents.**
*A symlink shares files. It does not guarantee the rule is applied the same way.*

`ssoty` reads the rule surfaces of multiple agent harnesses (Claude Code, Codex, …)
and finds — **deterministically, with no LLM and no network** — where shared rules
silently fail to apply across a harness boundary, then quantifies the per-turn token
cost ("Context Tax").

---

## The problem

Teams symlink one `AGENTS.md` / `CLAUDE.md` / rule set into every tool to get a
"single source of truth." But a symlink is a **distribution** mechanism, not a
**coherence** mechanism. The same canonical file can:

- load **always-on** in one harness (injected every turn) but **skill-gated** in
  another (loaded only when a skill triggers) — same file, unequal guarantee;
- reference a sibling rule that exists in one harness but was **never distributed**
  to the other — a broken pointer across the boundary;
- be duplicated across files, paying token rent every turn.

These are invisible until an agent in harness B quietly ignores a rule you "share."

## What ssoty does

```
$ uvx ssoty audit examples/messy-setup
ssoty audit — 2 Critical, 3 Warning, 6 FYI

  [Critical] broken_symlink (claude-code)
      .../.claude/rules/broken-link.md
      symlink target does not resolve: ./nope.md

  [Critical] dangling_cross_ref (codex)
      .../.codex/skills/global-agent-rules/references/shared-style.md
      references 'team-rules.md', which exists in another harness but is NOT
      loaded by 'codex' — broken pointer across the harness boundary

  [Warning] load_asymmetry (claude-code+codex)
      shared-style.md
      same rule loads differently per harness (claude-code=always-on,
      codex=skill-gated) — shared file, unequal guarantee
  ...
  [FYI] dangling_cross_ref (codex)
      references 'meta-layout.md' (absent here, intentional per .ssotyignore)
```

It distinguishes a **genuine** broken cross-reference (Critical) from
**intentional** non-sharing you declared in `.ssotyignore` (FYI) — precision over
noise.

## Context Tax (reproducible before/after)

```
$ uvx ssoty metrics examples/messy-setup     $ uvx ssoty metrics examples/clean-setup
  claude-code:                                  claude-code:
      always-on  : 206 tokens                       always-on  : 149 tokens   (-27.7%)
  codex:                                        codex:
      skill-gated: 106 tokens                       skill-gated:   0 tokens
```

Numbers are reported **per harness and never summed across harnesses**: `always-on`
(actual, every turn) and `skill-gated` (potential, only when a skill fires) are
different load guarantees. Compare *within* one harness, before vs after a cleanup.
Token counts use `tiktoken` when installed, otherwise a clearly-labelled `char/4`
heuristic.

Reproduce: `uvx ssoty metrics examples/messy-setup` (see [`benchmarks/REPORT.md`](benchmarks/REPORT.md)).

## Checks

| Check | Severity | What it catches |
|---|---|---|
| `broken_symlink` | Critical | symlinked rule whose target is gone |
| `dangling_cross_ref` | Critical / FYI | a rule references a sibling absent in this harness (FYI if declared intentional) |
| `load_asymmetry` | Warning | same rule, different load basis per harness |
| `duplicate_content` | Warning | identical blocks duplicated across files (token rent) |
| `non_shared_surface` | FYI | a rule present in one harness only |
| `skill_integrity` | Warning | skill dir without a `SKILL.md` |

## Install

```bash
# zero-install run
uvx ssoty audit                 # audits $HOME (~/.claude, ~/.codex)
# or install
pipx install ssoty
ssoty audit --redact            # mask home paths + emails in output
ssoty audit --ci                # exit non-zero on any Critical (for CI)
```

### CI (GitHub Action)
```yaml
- uses: snowlaxc/ssoty@v0
  with: { path: . }             # runs `ssoty audit --ci`
```

### Harness adapters (optional)
Thin wrappers so you can run ssoty from inside an agent:
- **Claude Code**: copy `adapters/claude-code/skills/ssoty` into `~/.claude/skills/`
- **Codex**: copy `adapters/codex/skills/ssoty` into `~/.codex/skills/`

The CLI is the product; adapters just shell out to it.

## How it works
`ssoty` resolves each harness's effective rule surface from disk (which files load,
and whether always-on or skill-gated), then runs deterministic checks. No model
calls, no network — same input, same output. It is **harness-agnostic by design**:
a cross-harness tool shouldn't live inside one harness.

## Privacy
ssoty audits *your* config; its output can quote your rules verbatim. It runs
**entirely locally** (no hosted service). This repo ships **synthetic fixtures
only**. See [`SECURITY.md`](SECURITY.md). Never commit ssoty output to a public repo.

## Roadmap (phase 2)
`ssoty fix` (auto-dedup), opt-in live "canary" runtime probe, LLM semantic
conflict detection, Gemini support, marketplace packaging.

---

# ssoty (한국어)

**AI 코딩 에이전트용 정적 cross-harness 룰 정합성 감사기.**
*symlink는 파일을 공유할 뿐, 룰이 같은 방식으로 적용됨을 보장하지 않는다.*

`ssoty`는 여러 에이전트 하네스(Claude Code, Codex, …)의 룰 표면을 읽어 — **결정적,
LLM·네트워크 0** — 공유한 룰이 하네스 경계를 넘으며 조용히 적용 실패하는 지점을 찾고,
턴당 토큰 비용("Context Tax")을 정량화합니다.

## 문제
한 `AGENTS.md`/`CLAUDE.md`/룰셋을 모든 도구에 symlink해 "single source of truth"를
만들지만, symlink는 **배포(distribution)** 수단이지 **정합성(coherence)** 수단이
아닙니다. 같은 canonical 파일이라도:

- 한 하네스에선 **always-on**(매 턴 주입), 다른 하네스에선 **skill-gated**(스킬
  트리거 시에만) — 같은 파일, 다른 보장;
- 한 하네스에만 배포된 형제 룰을 참조 → **경계를 넘는 깨진 포인터**;
- 파일 간 중복 → 매 턴 토큰 임대료.

하네스 B의 에이전트가 "공유한" 룰을 조용히 무시하기 전까진 보이지 않습니다.

## 사용 예
```bash
uvx ssoty audit examples/messy-setup
```
실제 broken 참조(Critical)와 `.ssotyignore`로 선언한 **의도적** non-sharing(FYI)을
구분합니다 — 소음이 아니라 정밀도.

## Context Tax (재현 가능한 before/after)
```
claude-code always-on : 206 → 149 tokens (-27.7%)   # 중복 제거 + broken 문서 제거
codex      skill-gated : 106 →   0 tokens
```
숫자는 **하네스별로 분리 보고하며 절대 합산하지 않습니다**. `always-on`(actual,
매 턴)과 `skill-gated`(potential, 트리거 시)는 다른 로드 보장이라, *같은 하네스 안에서*
정리 전/후를 비교하세요. 토큰은 `tiktoken`이 있으면 사용, 없으면 명시적 `char/4` 근사.

## 체크 / 설치 / 어댑터
위 영어 표·설치 섹션과 동일. 핵심:
```bash
uvx ssoty audit            # $HOME(~/.claude, ~/.codex) 감사
ssoty audit --redact       # 출력의 홈경로·이메일 마스킹
ssoty audit --ci           # Critical 있으면 비정상 종료 (CI용)
```
CLI가 제품이고, Claude Code/Codex 스킬 어댑터는 코어를 호출하는 얇은 래퍼(선택).

## 개인정보
ssoty는 *당신의* config를 감사하며 출력이 룰을 그대로 인용할 수 있습니다. **전적으로
로컬 실행**(호스팅 서비스 없음). 이 레포는 **합성 fixture만** 포함합니다.
[`SECURITY.md`](SECURITY.md) 참고 — ssoty 출력을 공개 레포에 커밋하지 마세요.

## 로드맵 (phase 2)
`ssoty fix`(자동 dedup), opt-in live "canary" 런타임 probe, LLM 의미 충돌 탐지,
Gemini 지원, 마켓플레이스 패키징.
