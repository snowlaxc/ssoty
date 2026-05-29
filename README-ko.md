# ssoty

[English](README.md) | **한국어**

[![PyPI](https://img.shields.io/pypi/v/ssoty.svg)](https://pypi.org/project/ssoty/)
[![CI](https://github.com/snowlaxc/ssoty/actions/workflows/ci.yml/badge.svg)](https://github.com/snowlaxc/ssoty/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**AI 코딩 에이전트용 정적 cross-harness 룰 정합성 감사기.**
*symlink는 파일을 공유할 뿐, 룰이 같은 방식으로 적용됨을 보장하지 않는다.*

`ssoty`는 여러 에이전트 하네스(Claude Code, Codex, …)의 룰 표면을 읽어 — **결정적,
LLM·네트워크 0** — 공유한 룰이 하네스 경계를 넘으며 조용히 적용 실패하는 지점을 찾고,
턴당 토큰 비용("Context Tax")을 정량화합니다.

---

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

```
$ uvx ssoty audit examples/messy-setup
ssoty audit — 2 Critical, 3 Warning, 6 FYI

  [Critical] broken_symlink (claude-code)
      .../.claude/rules/broken-link.md
      symlink target does not resolve: ./nope.md

  [Critical] dangling_cross_ref (codex)
      .../.codex/skills/global-agent-rules/references/shared-style.md
      references 'team-rules.md', 다른 하네스엔 있지만 'codex'엔 로드 안 됨
      — 경계를 넘는 깨진 포인터

  [FYI] dangling_cross_ref (codex)
      references 'meta-layout.md' (여기 없음, .ssotyignore로 의도 선언됨)
```

실제 broken 참조(Critical)와 `.ssotyignore`로 선언한 **의도적** non-sharing(FYI)을
구분합니다 — 소음이 아니라 정밀도.

## Context Tax (재현 가능한 before/after)

```
claude-code · always-on : 206 → 149 tokens (-27.7%)   # 중복 제거 + broken 문서 제거
codex       · skill-gated: 106 →   0 tokens
```

숫자는 **하네스별로 분리 보고하며 절대 합산하지 않습니다**. `always-on`(actual,
매 턴)과 `skill-gated`(potential, 트리거 시)는 다른 로드 보장이라, *같은 하네스 안에서*
정리 전/후를 비교하세요. 토큰은 기본적으로 결정적 `char/4` 근사(어느 머신에서도 같은 값); `SSOTY_EXACT_TOKENS=1`로 `tiktoken` opt-in.

재현: `uvx ssoty metrics examples/messy-setup` ([`benchmarks/REPORT.md`](benchmarks/REPORT.md) 참고).

## 체크

| 체크 | Severity | 무엇을 잡나 |
|---|---|---|
| `broken_symlink` | Critical | target이 사라진 symlink 룰 |
| `dangling_cross_ref` | Critical / FYI | 이 하네스에 없는 형제 룰 참조 (의도 선언 시 FYI) |
| `load_asymmetry` | Warning | 같은 룰, 하네스마다 다른 로드 방식 |
| `duplicate_content` | Warning | 파일 간 동일 블록 중복 (토큰 임대료) |
| `non_shared_surface` | FYI | 한 하네스에만 존재하는 룰 |
| `skill_integrity` | Warning | `SKILL.md` 없는 스킬 디렉토리 |

## 설치

```bash
# 무설치 실행
uvx ssoty audit                 # $HOME(~/.claude, ~/.codex) 감사
# 또는 설치
pipx install ssoty
ssoty audit --redact            # 출력의 홈경로·이메일 마스킹
ssoty audit --ci                # Critical 있으면 비정상 종료 (CI용)
```

### CI (GitHub Action)
```yaml
- uses: snowlaxc/ssoty@v0
  with: { path: . }             # `ssoty audit --ci` 실행
```

### 하네스 어댑터 (선택)
에이전트 안에서 ssoty를 부르는 얇은 래퍼:
- **Claude Code**: `adapters/claude-code/skills/ssoty`를 `~/.claude/skills/`로 복사
- **Codex**: `adapters/codex/skills/ssoty`를 `~/.codex/skills/`로 복사

CLI가 제품이고, 어댑터는 그 CLI를 shell-out할 뿐입니다.

## 동작 원리
`ssoty`는 하네스별 effective 룰 표면을 디스크에서 해석(어떤 파일이, always-on인지
skill-gated인지)한 뒤 결정적 체크를 돌립니다. 모델 호출·네트워크 0 — 같은 입력, 같은
출력. **설계상 harness-agnostic**: cross-harness 도구는 한 하네스 안에 살면 안 됩니다.

## 지원 하네스
Claude Code (`~/.claude/rules`, `CLAUDE.md`), Codex (`AGENTS.md`,
`global-agent-rules`), Cursor (`.cursor/rules/*.mdc`의 `alwaysApply` frontmatter로
load 판별, legacy `.cursorrules`), GitHub Copilot (`.github/copilot-instructions.md`),
Gemini CLI (`GEMINI.md`, `~/.gemini/GEMINI.md`). 비어있는 하네스는 스킵. `$HOME` 또는 프로젝트 루트를 가리키면 됩니다.

## 개인정보
ssoty는 *당신의* config를 감사하며 출력이 룰을 그대로 인용할 수 있습니다. **전적으로
로컬 실행**(호스팅 서비스 없음). 이 레포는 **합성 fixture만** 포함합니다.
[`SECURITY.md`](SECURITY.md) 참고 — ssoty 출력을 공개 레포에 커밋하지 마세요.

## 로드맵 (phase 2)
`ssoty fix`(자동 dedup), opt-in live "canary" 런타임 probe, LLM 의미 충돌 탐지,
Gemini 지원, 마켓플레이스 패키징.

## 배경
설계 근거는 [`docs/RFC.md`](docs/RFC.md)에 있습니다.

## 라이선스
[MIT](LICENSE)
