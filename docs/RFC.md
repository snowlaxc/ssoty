# Agent Harness Policy Coherence

## 요약

여러 에이전트 런타임과 하네스(예: omc, omx, Codex, Claude Code, Cursor)를 함께 쓰면, 단순히 성능 좋은 모델을 고르는 것만으로는 일관된 결과를 만들기 어렵다. 실제 품질은 다음 요소들이 동시에 맞물릴 때 결정된다.

- 어떤 모델을 어떤 작업에 쓸지 정하는 **model routing**
- 어떤 workflow/skill을 로드할지 정하는 **skill routing**
- AGENTS.md, CLAUDE.md, global rules, repo rules 같은 **rule surface**
- symlink, shared repo, generated files를 통한 **single source of truth** 관리
- 실행 중 권한, 도구, 검증, 상태를 통제하는 **runtime policy**

현재 공개 논의는 각각의 조각에는 존재하지만, 이들을 하나의 정합성 계층으로 묶는 표준은 아직 성숙하지 않았다. 이 문서는 그 문제를 **Policy/Skill Coherence Layer** 또는 **Agent Harness Policy Coherence** 문제로 정리한다.

## 문제의식

현대 coding agent 하네스에서 중요한 것은 모델 자체만이 아니라 모델을 둘러싼 실행 구조다.

하네스가 관리해야 하는 주요 축은 다음과 같다.

1. **Context assembly**
   - AGENTS.md, CLAUDE.md, repo-local rules, user-global rules, skill frontmatter, runtime overlays, memory, plan state를 어떤 순서와 우선순위로 주입하는가.
2. **Skill selection**
   - 사용자의 요청에 어떤 skill/workflow가 대응되는가.
   - skill이 항상-on rule과 충돌하지 않는가.
3. **Rule precedence**
   - system/developer/user instruction, global rule, repo AGENTS.md, nested AGENTS.md, skill body, tool-specific rule의 우선순위가 명확한가.
4. **Model routing**
   - 탐색, 계획, 구현, 디버깅, 리뷰, 검증에 서로 다른 모델과 reasoning effort를 배정하는가.
5. **Tool and permission gating**
   - shell, MCP, browser, subagents, external services, destructive commands, network access를 어떤 정책으로 허용/차단/승인 요청하는가.
6. **Verification gate**
   - 완료 판단을 모델의 자기평가가 아니라 tests, lint, typecheck, build, static analysis, browser verification, source citations 같은 evidence로 닫는가.
7. **State and provenance**
   - plan, progress, checkpoints, compaction recovery, commit context, decision record가 추적 가능한가.

## Symlink 기반 SSOT의 장점과 한계

현재 많은 사용자는 `AGENTS.md`, `CLAUDE.md`, `.agents/skills`, `.claude/skills`, `.cursor/rules` 등을 symlink하거나 shared repo에서 복사/생성해 관리한다.

이 방식의 장점:

- 한 파일 또는 한 repo를 **single source of truth**로 만들 수 있다.
- agent별 파일 복사본의 drift를 줄인다.
- 여러 런타임이 같은 skill/rule asset을 공유할 수 있다.
- 로컬 실험과 개인 workflow 관리가 빠르다.

하지만 symlink는 **distribution mechanism**이지 **coherence mechanism**은 아니다.

해결하지 못하는 것:

- 링크 대상이 실제로 존재하고 각 런타임이 읽는가.
- tool별 context assembly 방식 차이로 semantic drift가 생기지 않는가.
- skill body가 always-on rule 또는 repo rule과 충돌하지 않는가.
- nested AGENTS.md 우선순위가 의도대로 적용되는가.
- skill이 요구하는 tool 권한이 current policy와 맞는가.
- model routing 정책이 skill/rule의 위험도와 정합적인가.
- 사용자가 “같은 rule”이라고 생각하는 것이 각 agent에서 같은 의미로 실행되는가.

## 공개 논의 현황

### 1. AGENTS.md / CLAUDE.md / tool-specific rule drift

공개 가이드들은 이미 `AGENTS.md`와 `CLAUDE.md` 또는 Copilot instructions를 symlink해서 중복과 drift를 줄이라고 권장한다. 예를 들어 SSW Rules는 `AGENTS.md`를 cross-tool standard로 두고, Claude Code나 Copilot이 기대하는 파일명으로 symlink하는 방식을 설명한다. 또한 `.claude/` 전체가 아니라 shared item만 symlink하라고 경고한다.

관련 출처:

- SSW Rules — “Do you symlink your AGENTS.md to other tool-specific files?”  
  https://www.ssw.com.au/rules/symlink-agents-to-claude

### 2. Composable / centralized AGENTS.md

Codex 쪽에서는 `@include` 같은 composable AGENTS.md 기능 요청이 올라와 있다. 이 논의는 shared instructions, centralized policy, named AGENTS variants, 특정 AGENTS.md path 지정, global instructions 무시 같은 문제와 연결된다.

중요한 점은, 해당 논의가 skills를 reusable workflow로 보고, reusable instructions 문제는 별도로 남는다고 본다는 점이다.

관련 출처:

- OpenAI Codex issue #17401 — “feat: @include directive for composable AGENTS.md files”  
  https://github.com/openai/codex/issues/17401

### 3. Skills shared directory와 agent-specific directory 문제

Vercel Labs `skills` repo에는 global canonical directory에 skill이 설치되었지만 Claude Code가 읽는 directory symlink가 생성되지 않아 skill이 보이지 않는 문제가 올라와 있다. 이는 shared skill registry와 agent-specific load path 사이의 coordination 문제가 실제 bug로 나타난 사례다.

관련 출처:

- vercel-labs/skills issue #851 — “installs to ~/.agents/skills/ without creating ~/.claude/skills/ symlink”  
  https://github.com/vercel-labs/skills/issues/851

### 4. Cross-agent skill sharing

일부 문서는 SKILL.md를 cross-agent artifact로 보고, copy, symlink, shared git repo를 통해 Claude Code, OpenClaw, Codex CLI, Cursor, Gemini CLI 등에 공유하는 방식을 제안한다.

관련 출처:

- Agensi — “How to Share SKILL.md Skills Across AI Agents”  
  https://www.agensi.io/learn/how-to-share-skills-across-ai-agents

### 5. Rule/skill audit 도구의 등장

AgentLint 같은 도구는 `CLAUDE.md`, `AGENTS.md`, Cursor rules, skills, hooks, subagents, harness configs를 하나의 agent-rules surface로 보고 audit하려는 방향을 제시한다. Reddit에도 rules surface가 커지면서 contradiction, broken pointer, harness mismatch를 잡는 GitHub App을 만들었다는 논의가 있다.

관련 출처:

- AgentLint  
  https://agentlint.net/
- Reddit discussion — “Built a GitHub App that audits your CLAUDE.md...”  
  https://www.reddit.com/r/ClaudeAI/comments/1t6dmgn/built_a_github_app_that_audits_your_claudemd_on/

### 6. Skills as supply-chain risk

최근에는 skills를 prompt bundle이 아니라 supply-chain asset으로 다뤄야 한다는 보안/거버넌스 논의도 나오고 있다. 핵심 키워드는 ownership, provenance, versioning, review, scanning, permission review, auditability, policy enforcement다.

관련 출처:

- TechRadar Pro — “AI agent skills are becoming the next enterprise supply chain risk”  
  https://www.techradar.com/pro/ai-agent-skills-are-becoming-the-next-enterprise-supply-chain-risk-heres-how-to-govern-them

## 아직 부족한 지점

현재 공개 논의는 다음을 각각 다룬다.

- files drift를 줄이기 위한 symlink
- AGENTS.md composition
- skill sharing
- skill/rule linting
- enterprise skill governance
- model routing
- runtime approval/safety

하지만 이들을 하나의 policy coherence problem으로 묶는 표준적 계층은 아직 약하다.

즉, 다음 질문에 일관되게 답하는 시스템이 부족하다.

- 이 요청에는 어떤 skill이 활성화되어야 하는가?
- 그 skill은 현재 적용되는 global/repo/local rule과 충돌하지 않는가?
- 이 skill이 요구하는 tools/permissions는 current policy에서 허용되는가?
- 이 작업은 어떤 model tier와 reasoning effort가 적절한가?
- 실패하거나 불확실할 때 어떤 escalation path를 타야 하는가?
- 완료 조건은 어떤 evidence로 검증해야 하는가?
- 실제 런타임이 읽은 rule/skill/model config는 사용자가 의도한 SSOT와 같은가?

## 제안: Policy/Skill Coherence Layer

하네스에는 symlink manager 위에 별도의 coherence layer가 필요하다.

### 입력

- User request
- Active runtime identity: omc, omx, Codex, Claude Code, Cursor 등
- Resolved rule set
- Available skills and frontmatter
- Skill bodies and referenced assets
- Tool permission policy
- Model routing table
- Verification policy
- Current repo metadata
- Runtime state and checkpoints

### 처리 단계

1. **Resolve**
   - 실제로 로드되는 AGENTS.md/CLAUDE.md/rules/skills/hooks/subagents/configs를 펼친다.
2. **Normalize**
   - tool-specific 파일들을 공통 policy graph로 변환한다.
3. **Classify intent**
   - user request를 task type, risk, complexity, required evidence로 분류한다.
4. **Select candidates**
   - 후보 skills, roles, models, tools를 고른다.
5. **Check coherence**
   - skill ↔ rule, rule ↔ tool permission, model ↔ risk, verification ↔ claim 사이의 충돌을 검사한다.
6. **Plan execution contract**
   - 실행 전에 사용할 skill, model tier, tool 권한, verification gate, escalation rule을 결정한다.
7. **Audit after execution**
   - 실제 사용된 skill/rule/tool/model/evidence를 기록하고 drift를 감지한다.

### 출력

- Selected skill/workflow
- Effective rule summary
- Model routing decision
- Tool permission decision
- Required verification evidence
- Detected conflicts/warnings
- Runtime-readiness score
- Provenance/audit record

## 가능한 명령 UX

```bash
ssoty audit
ssoty links
ssoty explain --task "refactor cleanup"
ssoty resolve --agent codex
ssoty resolve --agent claude-code
ssoty diff --agent codex --agent claude-code
ssoty check-skill code-review
ssoty check-policy --skill deploy-pipeline --repo ./my-repo
ssoty graph --format mermaid
```

## 최소 구현 아이디어

### Manifest

```yaml
bundle:
  id: ethan-agent-policy
  version: 0.1.0
  source: ~/workspace/vc/ssoty

exports:
  rules:
    - id: global-agent-rules
      path: rules/global/AGENTS.md
  skills:
    - id: code-review
      path: skills/code-review/SKILL.md
  prompts:
    - id: executor
      path: prompts/executor.md

consumers:
  - id: omx
    rules:
      - ~/.codex/AGENTS.md
      - ~/.codex/skills/global-agent-rules
  - id: claude-code
    rules:
      - ~/.claude/CLAUDE.md
      - ~/.claude/skills

invariants:
  - every_symlink_target_exists
  - every_skill_has_skill_md
  - no_duplicate_skill_ids
  - no_conflicting_rule_ids
  - destructive_actions_require_escalation
  - refactor_requires_regression_tests_when_coverage_missing
```

### Policy graph

Nodes:

- Rule
- Skill
- Agent/runtime
- Model
- Tool
- Permission
- Verification gate
- State artifact

Edges:

- `exports_to`
- `symlinked_to`
- `loaded_by`
- `activates`
- `requires_tool`
- `requires_model_tier`
- `requires_verification`
- `overrides`
- `conflicts_with`

### Checks

- Broken symlink
- Skill without SKILL.md
- Duplicate skill IDs with different bodies
- Always-on rule contradicted by skill body
- Skill references missing asset/script
- Skill requires tool not available in runtime
- Skill requires network but policy says restricted
- Destructive command allowed without escalation
- Model routing table references unavailable model
- Repo-local AGENTS.md overrides global rule silently
- Generated file differs from source fragment

## Naming candidates

- Policy/Skill Coherence Layer
- Agent Harness Policy Coherence
- Agent Context Governance Layer
- Agent SSOT Auditor
- Runtime Policy Resolver
- Skill/Rule Consistency Engine

## Positioning

이 문제는 “skill manager”보다 크고, “AGENTS.md linter”보다 크며, “model router”보다 크다.

정확한 포지션은 다음과 같다.

> 여러 agent/runtime이 공유하는 instructions, skills, models, tools, permissions, verification gates를 하나의 policy graph로 해석하고, 실행 전후 정합성을 검증하는 하네스 계층.

## 결론

symlink 기반 SSOT는 좋은 출발점이다. 하지만 symlink는 공유를 보장할 뿐, 해석의 일관성이나 실행 정책의 안전성을 보장하지 않는다.

다음 단계는 다음을 통합하는 것이다.

- `skill-router`
- `rule-auditor`
- `model-router`
- `tool-permission-gate`
- `verification-gate`
- `runtime-state/provenance recorder`

이 통합 계층이 있어야 여러 에이전트 하네스를 동시에 쓰는 환경에서 “같은 정책을 공유한다”가 아니라 “같은 정책으로 실행된다”를 검증할 수 있다.
