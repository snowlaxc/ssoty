# ssoty

[English](README.md) | **한국어**

[![PyPI](https://img.shields.io/pypi/v/ssoty.svg)](https://pypi.org/project/ssoty/)
[![CI](https://github.com/snowlaxc/ssoty/actions/workflows/ci.yml/badge.svg)](https://github.com/snowlaxc/ssoty/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**AI 코딩 에이전트용 정적 cross-harness 룰 발산(divergence) 감사기.**
*두 모델, 하나의 "공유" 룰셋 — 그런데 정말 같은 룰로 동작할까? 대개 아니다.*

`ssoty`는 8개 에이전트 하네스(Claude Code, Codex, Cursor, Copilot, Gemini, Cline,
Windsurf, Continue)의 effective 룰 표면을 읽어 — **결정적, LLM·네트워크 0** — 두
모델이 어디서 갈라지는지 보여줍니다: 한 모델만 적용하고 다른 모델은 못 보는 룰,
공유하지만 *다른 보장*(always-on vs skill-gated)으로 로드되는 룰, 같은 이름인데 복사본이
조용히 **다른 내용으로 drift**한 룰, 경계를 넘으며 깨지는 cross-reference. 턴당 토큰
비용("Context Tax")은 **부가 측정**으로 함께 제공합니다.

---

## 문제

Claude Code, Codex, Cursor에 하나의 "공유" 룰셋을 물려놓고 동일한 동작을 기대하지만,
실제론 동일하게 동작하지 않습니다 — 각 하네스가 **서로 다른 effective 룰셋**을
해석하기 때문입니다. 같은 canonical 파일이라도:

- 한 하네스에선 **always-on**(매 턴 주입), 다른 하네스에선 **skill-gated**(스킬
  트리거 시에만) — 같은 파일, 다른 보장;
- 한 하네스에만 배포된 형제 룰을 참조 → **한쪽에선 풀리지만 다른 쪽에선 안 풀리는 포인터**;
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

로드 *방식*(존재/부재, always-on vs skill-gated)뿐 아니라 **내용 발산(content drift)**도
잡습니다: 두 하네스가 **같은 파일명이지만 별개 복사본에서 다른 텍스트**(서로 다른
`realpath`)를 가질 때 `same rule, divergent content` 카테고리가 발화합니다 — symlink 대신
복사로 만들어 두 모델이 "같은" 룰의 서로 다른 버전을 조용히 강제하는 전형적 실수입니다.
symlink로 공유된 단일 진실 출처(SSOT)는 하나의 `realpath`를 공유해 byte-identical이므로
이 검사에 걸리지 않습니다 — SSOT collapse가 *일어나지 않은* 경우에만 정확히 발화합니다.

## 사용 예

```
$ uvx ssoty audit examples/messy-setup
ssoty audit — 1 Critical, 3 Warning, 5 FYI

  [Critical] broken_symlink (claude-code)
      .../.claude/rules/broken-link.md
      symlink target does not resolve: ./nope.md

  [Warning] dangling_cross_ref (codex)
      .../.codex/skills/global-agent-rules/references/shared-style.md
      references 'team-rules.md' — 다른 하네스엔 있지만 여기엔 로드 안 됨;
      이 하네스 컨텍스트에서 포인터가 도달 가능한지 확인하세요

  [FYI] dangling_cross_ref (codex)
      references 'meta-layout.md' (여기 없음, .ssotyignore로 의도 선언됨)
```

실제 하네스 간 발산(Warning)과, `.ssotyignore`로 선언한 **의도적** non-sharing /
canonical로 공유된(symlink) 포인터 / 하네스별 entrypoint(모두 FYI)를 구분합니다 — 소음이
아니라 정밀도. 유일한 구조적 `Critical`은 `broken_symlink`(target이 사라진 symlink)이므로,
`--ci`는 의도적 SSOT 레이아웃이 아니라 *진짜로 깨진 설정*에만 차단을 겁니다.

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
| `broken_symlink` | Critical | target이 사라진 symlink 룰 (유일한 구조적 Critical) |
| `dangling_cross_ref` | Warning / FYI | 이 하네스에 없는 형제 룰 참조 (Warning = 실제 하네스 간 발산; 의도 선언·canonical symlink 공유·하네스별 entrypoint·어디에도 없음이면 FYI) |
| `load_asymmetry` | Warning | 같은 룰, 하네스마다 다른 로드 방식 |
| `content_divergence` | Warning | ≥2 하네스에 같은 룰 *이름*이 있으나 별개 복사본(서로 다른 `realpath`)의 **내용**이 다름 — symlink 대신 복사로 인한 drift; symlink로 공유된 SSOT(같은 `realpath`)와 broken symlink은 제외 |
| `duplicate_content` | Warning / FYI | 하네스 *내* 동일 블록 중복(Warning = 토큰 임대료); 하네스 *간* 예상된 SSOT 공유는 하나의 FYI로 롤업 |
| `non_shared_surface` | FYI | 한 하네스에만 존재하는 non-entrypoint 룰 (하네스별 entrypoint는 제외) |
| `skill_integrity` | Warning | `SKILL.md` 없는 스킬 디렉토리 |
| `weak_directive` | FYI | always-on 룰의 한 줄에서 약한 표현(`should`, `try to`, …)이 강한 요구 신호(`never`, `security`, …)를 흐리는 경우 |

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

### Sync — 감사자에서 관리자로 (dry-run + 백업 우선)
`ssoty audit`는 하네스가 *갈라졌다고 알려준다*. `ssoty sync`는 *원인을 고친다*:
**하나의 정규 룰 소스**를 모든 하네스 타깃에 심볼릭 링크로 배포해, 모든 모델이
byte-identical한 파일(같은 inode)을 가리키게 만들어 divergence를 근본에서 무너뜨린다.
감사자가 관리자가 된다 — 그리고 **sync가 쓰는 것이 곧 audit가 읽는 것**이므로 `audit`가
자연스러운 사후 검증이 된다.

```bash
ssoty sync                      # DRY-RUN: 정확한 링크 계획만 출력, 아무것도 안 씀
ssoty sync --apply              # 심볼릭 링크 생성/교체; 교체 대상은 먼저 백업
ssoty sync --manifest ssoty.json --apply
ssoty sync --apply && ssoty audit --ci   # 배포 후 CI에서 일관성 증명
```

Sync는 **`ssoty.json` manifest**(표준 라이브러리 JSON만 — 추가 의존성 없음)로 구동된다.
읽기 전용 정규 `source` 트리와 그것이 링크될 하네스별 `target` 경로를 기술한다. 디렉터리
타깃은 해석된 소스 basename마다 심볼릭 링크 하나씩, `CLAUDE.md` 같은 단일 파일 타깃은 링크
하나를 받는다. [`examples/ssoty.json`](examples/ssoty.json) 참고.

`ssoty fix`와 동일한 하드 안전장치: **기본이 dry-run**(정확한 계획만 출력, 아무것도 안 쓰고
백업 디렉터리도 안 만듦), `--apply`만 변경한다. `--apply` 시, 기존 실파일이나 다른 곳을
가리키는 심볼릭 링크를 교체하기 전에 그 노드를 `.ssoty-backup/<timestamp>/`(상대경로 보존)로
백업한다 — link-aware라 교체되는 심볼릭 링크의 옛 타깃 문자열도 복구 가능하다. manifest에
선언된 `target` 경로만 쓰고(루트를 벗어나는 타깃은 쓰기 전에 거부, exit 2), 정규 `source`는
읽기 전용으로 취급한다. **idempotent**하며(두 번째 `--apply`는 순수 no-op, 새 백업 없음),
*자기 자신이 만든* orphan 심볼릭 링크(정규 소스를 가리키지만 타깃이 사라진 링크)만 정리한다 —
사용자의 무관한 심볼릭 링크는 절대 건드리지 않는다. `--method symlink`가 기본이자 현재
유일한 방법이다.

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
legacy `.clinerules`, `AGENTS.md`), Windsurf (`.windsurf/rules/*.md`, legacy
`.windsurfrules`), Continue (`.continue/rules/*.md`). 비어있는 하네스는 스킵.
`$HOME` 또는 프로젝트 루트를 가리키면 됩니다.

## 개인정보
ssoty는 *당신의* config를 감사하며 출력이 룰을 그대로 인용할 수 있습니다. **전적으로
로컬 실행**(호스팅 서비스 없음). 이 레포는 **합성 fixture만** 포함합니다.
[`SECURITY.md`](SECURITY.md) 참고 — ssoty 출력을 공개 레포에 커밋하지 마세요.

## 로드맵 (phase 2)
`ssoty sync` 자동 dedup, `symlink`에 더해 `copy` 방식, opt-in live "canary" 런타임
probe, LLM 의미 충돌 탐지, Gemini 지원, 마켓플레이스 패키징.

## 배경
설계 근거는 [`docs/RFC.md`](docs/RFC.md)에 있습니다.

## 라이선스
[MIT](LICENSE)
