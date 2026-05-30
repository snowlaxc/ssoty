# ssoty

[English](README.md) | **한국어**

[![PyPI](https://img.shields.io/pypi/v/ssoty.svg)](https://pypi.org/project/ssoty/)
[![CI](https://github.com/snowlaxc/ssoty/actions/workflows/ci.yml/badge.svg)](https://github.com/snowlaxc/ssoty/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**AI 코딩 에이전트용 정적 cross-harness 룰 발산(divergence) 감사기.**
*두 모델, 하나의 "공유" 룰셋 — 그런데 정말 같은 룰로 동작할까? 대개 아니다.*

`ssoty`는 여러 에이전트 하네스(Claude Code, Codex, Cursor, Copilot, Gemini, Cline)의
effective 룰 표면을 읽어 — **결정적, LLM·네트워크 0** — 두 모델이 어디서 갈라지는지
보여줍니다: 한 모델만 적용하고 다른 모델은 못 보는 룰, 공유하지만 *다른 보장*(always-on
vs skill-gated)으로 로드되는 룰, 경계를 넘으며 깨지는 cross-reference. 턴당 토큰
비용("Context Tax")은 **부가 측정**으로 함께 제공합니다.

---

## 문제

Claude Code, Codex, Cursor에 하나의 "공유" 룰셋을 물려놓고 동일한 동작을 기대하지만,
실제론 동일하게 동작하지 않습니다 — 각 하네스가 **서로 다른 effective 룰셋**을
해석하기 때문입니다. 같은 canonical 파일이라도:

- 한 하네스에선 **always-on**(매 턴 주입), 다른 하네스에선 **skill-gated**(스킬
  트리거 시에만) — 같은 파일, 다른 보장;
- 한 하네스에만 배포된 형제 룰을 참조 → **경계를 넘는 깨진 포인터**;
- 파일 간 중복 → 매 턴 토큰 임대료.

결과적으로 같은 프롬프트, 같은 레포인데 **모델마다 effective 룰이 다르고** — 그래서
일관성 없이 동작하며, 한 모델이 "공유한" 룰을 조용히 무시하기 전까진 보이지 않습니다.

## 룰 발산 (헤드라인)

```
$ uvx ssoty diff examples/messy-setup --a claude-code --b codex

  claude-code  vs  codex
      only in claude-code (1): team-rules.md
      same rule, different load (1):
          shared-style.md  claude-code=always-on  |  codex=skill-gated
      broken cross-references across the boundary (1):
          codex:shared-style.md -> 'team-rules.md'  (loads only in claude-code, NOT in codex)
      VERDICT: claude-code and codex do NOT operate under the same rules
               (1 rule only in claude-code, 1 loads differently, 1 broken cross-ref)
```

`ssoty diff`는 핵심 질문에 답합니다: *이 두 모델은 같은 룰로 동작하는가?* 현재 존재하는
모든 쌍에 대해(--a/--b 생략) 또는 지정한 두 하네스를 비교합니다. `--json`/`--redact`
지원, 명령은 엄격히 read-only입니다.

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

## 부가 측정: Context Tax (토큰 임대료)

부가 측정 — 각 표면의 턴당 토큰 비용과 매 턴 지불하는 중복 콘텐츠. 정리 전/후 비교에
유용하지만, *핵심 pitch는 위의 발산(divergence)*이지 토큰 임대료가 아닙니다.

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
uvx ssoty diff                  # cross-model 룰 발산 (헤드라인; 존재하는 모든 쌍)
uvx ssoty audit                 # $HOME(~/.claude, ~/.codex) 감사
# 또는 설치
pipx install ssoty
ssoty diff --a claude-code --b codex  # 지정한 두 하네스 비교 (read-only)
ssoty audit --redact            # 출력의 홈경로·이메일 마스킹
ssoty audit --ci                # Critical 있으면 비정상 종료 (CI용)
ssoty audit --format sarif      # SARIF 2.1.0 (github/codeql-action/upload-sarif용)
```

`--format {text,json,sarif}`로 audit 출력 형식 선택(기본 `text`); `--json`은
`--format json`의 하위호환 alias.

### Fix (dry-run + 백업 우선)
```bash
ssoty fix                       # DRY-RUN: 무엇이 바뀔지만 출력, 아무것도 안 씀
ssoty fix --apply               # 안전한 수정 수행; 손대는 파일을 먼저 모두 백업
ssoty fix --apply --scaffold-ignore   # 비공유 룰 이름을 .ssotyignore에 추가까지
```

`ssoty fix`는 **기본이 dry-run**이다 — 무엇을 할지 그대로 출력하고 아무것도 바꾸지
않는다. `--apply`를 줘야만 쓰며, 그때도 손댈 파일을 먼저 감사 루트 아래 타임스탬프
백업 디렉터리(`.ssoty-backup/<timestamp>/`, 상대경로 보존)로 복사하고 그 위치를
출력한다. **안전한** 수정만 한다: 깨진 심볼릭 링크 제거(타깃이 해석되지 않으므로
실제 내용 손실 없음), 그리고 `--scaffold-ignore` 시 의도적으로 비공유인 룰 이름을
`.ssotyignore`에 기록. 실제 룰 파일을 편집하지 않고, 정상 심볼릭 링크를 건드리지
않으며, idempotent하다(다시 실행해도 아무 일 없음). 백업이 커밋되지 않도록
`.ssoty-backup/`를 gitignore에 추가하라.

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
Gemini CLI (`GEMINI.md`, `~/.gemini/GEMINI.md`), Cline (`.clinerules/` 디렉토리,
legacy `.clinerules`, `AGENTS.md`). 비어있는 하네스는 스킵. `$HOME` 또는 프로젝트 루트를 가리키면 됩니다.

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
