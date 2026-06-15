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

### Init — manifest 스캐폴딩 (zero to `ssoty.json`)
`sync`가 처음인가? `ssoty init`은 루트에 *이미 존재하는* 하네스를 탐지해 시작용
`ssoty.json`을 대신 써준다 — `sync`로 가는 한 줄짜리 on-ramp이다:

```bash
ssoty init                      # PREVIEW: 하네스 탐지, 제안 ssoty.json 출력, 아무것도 안 씀
ssoty init --apply              # ./ssoty.json 작성 (기존 파일은 덮어쓰기 거부)
ssoty init --apply --force      # 기존 ssoty.json 덮어쓰기
ssoty init && ssoty sync        # 스캐폴딩 후 링크 계획 미리보기
```

`init`은 **`audit`와 동일한 탐지**(resolve_all, 두 번째 파일시스템 walk 없음)를 재사용하므로,
실제 룰 문서가 해석된 하네스만 정확히 스캐폴딩한다. 룰이 이미 공유 디렉터리로 심볼릭 링크돼
있으면 그 디렉터리를 정규 `common` 소스로 추론하고 각 하네스에 `"common": true`를 단다.
추론이 안 되면 `_comment`가 달린 안전한 **placeholder** common 소스를 출력한다. **기본이
preview**(아무것도 안 씀)이고, **오직** `ssoty.json`만 쓰며(룰 파일은 절대 건드리지 않음),
기존 manifest는 `--force` 없이 **덮어쓰지 않는다**. 출력된 manifest는 그대로 `sync`로
round-trip된다.

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

### Adopt — 흩어진 복사본에서 정규 SSOT 부트스트랩 (dry-run + 백업 우선)
`init`/`sync` 이전 단계가 `adopt`다. 루트에 존재하는 하네스를 스캔해 같은 이름의 룰을 분류하고,
정규 레이아웃을 제안한다 — **두 개 이상 하네스에서 내용이 바이트 단위로 동일한** 룰은
`common/<name>`, 한 하네스에만 있는 룰은 `<harness>/<name>`. `init`이 추론하고 `sync`가 배포하는
**정규 소스를 만들어내는** 단계로, "복사본이 사방에 흩어진" 설정을 한 번에 단일 SSOT로 정리한다.

```bash
ssoty adopt                       # 인터랙티브 TUI (터미널): 룰 분류 후 'a'로 적용
ssoty adopt --plan                # 비인터랙티브 TEXT 미리보기 (CI/파이프용): 분류만, 쓰지 않음
ssoty adopt --no-tui              # 터미널에서도 텍스트 경로 강제
ssoty adopt --apply               # canonical home(기본 ~/.ssoty)으로 룰 통합, 원본을 심볼릭 링크로 교체, 백업 우선
ssoty --home ~/my-rules adopt --apply        # 커스텀 canonical home 사용(+ 영속 저장)
ssoty adopt --apply --no-symlink-originals   # 이동/복사만; 원본은 실제 파일로 유지
ssoty adopt --canonical-dir my-rules --apply # 일회성 정규 루트 오버라이드 (영속되지 않음)
ssoty adopt --apply && ssoty init && ssoty sync   # 전체 라이프사이클
```

#### Canonical home (`~/.ssoty/`)
`adopt`는 룰을 **canonical home** — 기본값 `~/.ssoty/` — 으로 통합한다. 글로벌 `ssoty --home <경로>`로
다른 위치를 지정할 수 있고, 그 선택은 `${XDG_CONFIG_HOME:-~/.config}/ssoty/config.json`에 **영속 저장**되어
이후 모든 명령이 재사용한다(우선순위: `--home` 플래그 > 저장된 config > `~/.ssoty` 기본). home은 사용자가
지정한 신뢰된 위치이므로 **스캔 루트(`$HOME`) 밖에 있어도 된다**. 일회성 `--canonical-dir`은 영속 없이
그 실행만 오버라이드한다.

실제 터미널에서 `adopt`는 이제 **기본적으로 인터랙티브**다: 두 패널 TUI가 왼쪽에 분류된 룰
목록을, 오른쪽에 내용 미리보기 + 분류 선택기를 보여준다. 각 룰을 `common/`(모든 하네스) 또는 단일
`<harness>/`로 토글하고(`common`과 하네스별 선택은 상호 배타적) `a`로 적용, `q`로 종료한다. 선택기는
**룰이 이미 사본을 가진 하네스뿐 아니라 스캔된 모든 하네스를 타겟으로 제공**한다: codex 전용 룰도
`claude-code`로 배정할 수 있다(2개 이상 하네스를 선택하면 단일 공유 `common/<name>`으로 통합).
**DIVERGENT** 룰은 `common`으로 설정할 수 없다(선택기가 표시되지 않음 — 수동 해소 후 재실행). TUI는
자체적으로 파일을 **전혀 변경하지 않는다**: `a`를 누르면 선택을 바탕으로 plan을 재구성해 텍스트
경로와 **완전히 동일한** 결정적 엔진(동일한 이동/백업/심볼릭, 동일한 `--force` 가드)을 호출한다.
인터랙티브 프런트엔드는 [Textual](https://textual.textualize.io/)(코어 의존성)로 구동되며, 엔진
자체는 순수 stdlib로 유지된다. stdin/stdout이 모두 TTY가 아니거나(CI, 파이프), `--apply`/`--plan`/
`--no-tui`를 주면 `adopt`는 자동으로 비인터랙티브 텍스트 경로를 쓴다.

`adopt`는 감사자의 `content_divergence` 체크와 **완전히 동일한 내용-동일성 그룹화**를 재사용한다 —
각 이름을 `(realpath, 정규화된 내용)`으로 버킷팅하므로 이미 심볼릭 링크된 SSOT는 한 버킷으로 합쳐진다.
이름마다 네 가지 결과: **COMMON_CANDIDATE**(≥2 하네스에서 동일 → `common/`), **HARNESS_SPECIFIC**
(한 하네스 → `<harness>/`), **ALREADY_SHARED**(이미 단일 inode → 이동 없음), **DIVERGENT**(같은 이름,
*다른* 내용). 발산 룰은 **플래그만 하고 절대 자동 병합하지 않는다**: 모든 변형을 백업하고 원본은
그대로 두며, 변형별 짧은 내용 지문을 출력해 사용자가 의도적으로 충돌을 해소하게 한다 — `adopt`는
발산 집합에서 단일 `common/<name>`을 절대 쓰지 않는다. 하네스별 **엔트리포인트**
(`CLAUDE.md`/`AGENTS.md`/`GEMINI.md`/…)는 통합 대상에서 제외된다(각 하네스가 자기 복사본을 소유) —
그대로 둔다. 안전 계약은 `fix`/`sync`와 동일: **기본 preview**, `--apply`는 모든 이동/교체 노드를
변경 *전에* `.ssoty-backup/<timestamp>/`로 백업, canonical home은 신뢰된 위치라 스캔 루트 밖에 있어도
되며 엔진이 생성하는 목적지(`common/<name>` / `<harness>/<name>`)에는 `..`가 없어 home을 벗어나지 않음,
idempotent(재실행 시 이미 링크된 원본과 동일 내용 쓰기를 건너뜀), 내용이 다른 정규 목적지를 덮어쓸 때만
`--force` 필요.

### Add — 정규 SSOT에 새 룰 하나 추가
정규 소스가 생긴 뒤에는 `ssoty add`로 새 룰 하나를 올바른 위치에 넣어 정확히 전파시킨다:

```bash
ssoty add my-new-rule.md                         # 선택 없음 -> 후보 배치 출력, 추측하지 않음
ssoty add my-new-rule.md --common --apply        # 정규 common/에 기록 (모든 하네스로 sync)
ssoty add my-new-rule.md --harness codex --apply # 한 하네스의 자체 소스에 기록
ssoty add my-new-rule.md --common --apply && ssoty sync   # 그 후 배포
```

`add`는 정규 `common` 디렉터리와 하네스별 타깃을 `ssoty.json` manifest에서 읽는다(manifest가 아직 없으면
`agent-rules/common` 플레이스홀더로 폴백). `--common`과 `--harness`는 **상호 배타적**이며, 둘 다 없으면
`add`는 가능한 배치를 미리 보여주고 **추측을 거부**한다. 동일 안전 계약: **기본 preview**, `--apply`로 기록,
덮어쓸 때 백업 우선, 내용이 다르면 `--force` 필요, idempotent(동일 내용은 건너뜀), 목적지는 항상 루트 하위로
검증. 정확히 **파일 하나**만 쓰고 다음 명령(`ssoty sync`)을 출력한다 — 암묵적 체이닝 없음, 사용자가 제어를 유지.

> **라이프사이클:** `adopt` → `init` → `add` → `sync` → `audit`. `adopt`가 정규 소스를 만들고,
> `init`이 그것을 참조하는 manifest를 스캐폴딩하며, `add`가 새 룰을 넣고, `sync`가 전부 심볼릭 링크로
> 배포하고, `audit`이 일관성을 증명한다.

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
