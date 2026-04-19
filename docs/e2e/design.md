# claude-code-python E2E 技术设计

> 对标 [`agent-sdk/tests/e2e`](../../../agent-sdk/tests/e2e) 的真实 Claude CLI 测试体系。  
> 主 SDK 设计见 [../design.md](../design.md)。  
> parser 子包设计见 [../parser/design.md](../parser/design.md)。

---

## 1. 目标与边界

本文件只描述 **真实 Claude CLI E2E** 设计，不再重复主设计里的 SDK 分层、parser 实现与
unit test 细节。

E2E 的职责只有三类：

1. 验证 Python SDK 与真实 Claude CLI 的端到端连通性
2. 验证 auth / session / streaming / image / prompt / spawn args / agents 等关键用户路径
3. 产出可对拍的 artifacts，便于和 `agent-sdk`、`claude-code-go` 做横向比对

测试迁移沿用主设计中的两类标准：

- **literal-port**：CLI 路径、spawn args、artifact 文件名、helper 语义、环境隔离等必须一致
- **semantic-port**：模型输出的宽松语义断言（如“能识别出至少 3 个图形”）要求行为等价

---

## 2. 目录布局

```text
claude-code-python/
├── tests/
│   └── e2e/
│       ├── conftest.py
│       ├── config.py
│       ├── harness.py
│       ├── reporters.py
│       ├── fixtures/
│       │   └── images/
│       │       ├── red-square.png
│       │       ├── shapes-demo.png
│       │       └── receipt-demo.png
│       └── cases/
│           └── test_real_cli.py
└── docs/
    └── e2e/
        └── design.md
```

说明：

- `fixtures/images/*` 采用从 `agent-sdk/tests/e2e/fixtures/images/` **字节级复制**
- `cases/test_real_cli.py` 对标 Node 版 `real-cli.test.ts`
- `config.py` / `harness.py` / `reporters.py` 不是自由发挥的辅助文件，而是跨仓库对拍契约的一部分

---

## 3. 凭证加载与 setup 语义

E2E 配置从环境变量读取：

- `E2E_API_KEY`
- `E2E_AUTH_TOKEN`
- `E2E_BASE_URL`
- `E2E_MODEL`

与 `agent-sdk` 一致，无凭证时 **不能静默 skip**。必须保留一个 setup-failure 用例：

- `test_requires_e2e_env_vars`

其意义不是“让 CI 失败”，而是保留与上游完全一致的测试语义，让调用方清楚看到：

- 真实 E2E 环境尚未配置
- 不是测试用例被隐藏了

---

## 4. 真实 CLI 路径约束

真实 E2E 默认优先解析 **仓库内安装** 的 Claude CLI，而不是直接依赖宿主机 PATH 上的
`claude` 可执行文件。

必须保留以下约束：

- raw `spawn` 事件中的 `command` / `args` 能体现仓库内 CLI 路径
- auth-path 测试里要断言 `spawn.command` 包含 `@anthropic-ai/claude-code/cli.js`
- 若未来允许覆盖 CLI 路径，也必须通过显式测试配置完成，不能默认漂移到系统全局安装

---

## 5. 执行模型与并发约束

E2E 默认串行执行。

原因：

- `with_optional_poisoned_env()` 会故意污染宿主 `ANTHROPIC_*` 环境变量
- artifact 输出依赖同一 run-id 目录布局
- 某些用例会创建临时目录、调试文件、plugin 目录和 probe 文件

因此当前阶段：

- 禁止 `pytest-xdist`
- 禁止手动并行调度 `tests/e2e`
- 若未来要并行，必须先把环境污染类用例改造成子进程隔离

---

## 6. Helper 契约

### 6.1 `config.py`

职责：

- 读取 `E2E_*` 环境变量
- 组装 `E2EConfig`
- 提供 `get_client_options()` / `list_available_auth_modes()`

默认 session 配置必须与 Node 版一致：

- `bare = True`
- `setting_sources = ""`
- `verbose = True`
- `include_partial_messages = True`
- `dangerously_skip_permissions = True`

### 6.2 `harness.py`

必须保留的 helper：

- `execute_buffered_case()`
- `execute_streamed_case()`
- `create_temp_workspace()`
- `write_probe_file()`
- `write_prompt_file()`
- `create_empty_plugin_dir()`
- `cleanup_path()`
- `get_spawn_event()`
- `get_flag_values()`
- `has_flag()`
- `read_debug_file()`
- `parse_json_response()`
- `with_optional_poisoned_env()`

其中两个 helper 需要特别严格对齐：

1. `parse_json_response()`  
   顺序必须是：
   - 先 `strip_code_fence()`
   - 再尝试直接 `json.loads()`
   - 失败后再 `extract_first_json_value()`

2. `with_optional_poisoned_env()`  
   必须通过污染宿主 `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` /
   `ANTHROPIC_BASE_URL` 来验证 SDK 不会继承隐式凭据

### 6.3 `reporters.py`

职责：

- 创建 `artifact_dir`
- 写入结构化产物
- 输出 `[E2E] key=value` 风格终端摘要

---

## 7. Artifact 契约

### 7.1 目录布局

artifact 根目录格式：

```text
tests/e2e/artifacts/<run-id>/<case-name>/
```

其中：

- `run-id = <ISO8601 替换冒号和点>-<pid>`
- `case-name` 需做文件名安全化处理

### 7.2 必须落盘的文件

每个 case 必须写入：

- `input.json`
- `relay-events.json`
- `raw-events.ndjson`
- `final-response.txt`
- `summary.json`
- `terminal-transcript.txt`

### 7.3 序列化格式

- `input.json` / `relay-events.json` / `summary.json`
  - 使用 2-space pretty JSON
  - 文件末尾补 `\n`
- `raw-events.ndjson`
  - 每行一个 JSON object
  - 文件末尾补 `\n`
- `terminal-transcript.txt`
  - 使用 `[E2E] key=value` 行格式
  - 至少包含 case、auth_mode、options、input、raw_event_count、relay_event_count、
    final_response、artifact_dir

---

## 8. 逐条映射附录

本附录对应
[agent-sdk/tests/e2e/cases/real-cli.test.ts](../../../agent-sdk/tests/e2e/cases/real-cli.test.ts)
的 14 条真实 CLI 用例。

1. **Source:** `real-cli.test.ts:68` `requires E2E env vars`。**Target:** `tests/e2e/cases/test_real_cli.py::test_requires_e2e_env_vars`。`literal-port`。要求无凭证时保留一个 setup-failure 用例，而不是静默 skip。
2. **Source:** `real-cli.test.ts:74` `loads local secrets and default session settings`。**Target:** `tests/e2e/cases/test_real_cli.py::test_loads_local_secrets_and_default_session_settings`。`literal-port`。要求 E2E 配置层读取到 model、`bare=True`、`setting_sources=""`、`include_partial_messages=True`。
3. **Source:** `real-cli.test.ts:85` `runs the apiKey path through ClaudeCode options when configured`。**Target:** `tests/e2e/cases/test_real_cli.py::test_runs_the_api_key_path_through_claude_code_options_when_configured`。`literal-port`。要求 API key 路径下返回 JSON 中 `auth_mode=api-key`，宿主脏环境不泄漏，spawn command 指向仓库内 Claude CLI。
4. **Source:** `real-cli.test.ts:114` `runs the authToken + baseUrl path through ClaudeCode options when configured`。**Target:** `tests/e2e/cases/test_real_cli.py::test_runs_the_auth_token_and_base_url_path_through_claude_code_options_when_configured`。`literal-port`。要求 auth-token + baseUrl 路径同样能成功，并返回 `auth_mode=auth-token`。
5. **Source:** `real-cli.test.ts:141` `preserves context across multiple run calls on the same session`。**Target:** `tests/e2e/cases/test_real_cli.py::test_preserves_context_across_multiple_run_calls_on_the_same_session`。`literal-port`。要求同一 `Session` 多轮保持上下文，且 `resume_session()` 跨实例恢复后仍能记住前文 token。
6. **Source:** `real-cli.test.ts:182` `emits text deltas when includePartialMessages is true`。**Target:** `tests/e2e/cases/test_real_cli.py::test_emits_text_deltas_when_include_partial_messages_is_true`。`literal-port`。要求 streaming 模式在开启 partial messages 时实际收到 `text_delta` 与 `turn_complete`。
7. **Source:** `real-cli.test.ts:204` `still completes when includePartialMessages is false`。**Target:** `tests/e2e/cases/test_real_cli.py::test_still_completes_when_include_partial_messages_is_false`。`literal-port`。要求关闭 partial messages 后仍正常结束，并能拿到最终文本。
8. **Source:** `real-cli.test.ts:225` `understands a simple red square image`。**Target:** `tests/e2e/cases/test_real_cli.py::test_understands_a_simple_red_square_image`。`literal-port`。要求图片走 `stream-json` 而不是 `--image`，模型输出中识别出 red + square。
9. **Source:** `real-cli.test.ts:252` `counts obvious shapes from a synthetic image`。**Target:** `tests/e2e/cases/test_real_cli.py::test_counts_obvious_shapes_from_a_synthetic_image`。`semantic-port`。要求模型至少识别出 3 个明显图形，并返回非空形状列表。
10. **Source:** `real-cli.test.ts:276` `extracts a visible snippet from a synthetic receipt image`。**Target:** `tests/e2e/cases/test_real_cli.py::test_extracts_a_visible_snippet_from_a_synthetic_receipt_image`。`semantic-port`。要求模型能从 receipt 图中提取一段明显可见的文本片段。
11. **Source:** `real-cli.test.ts:301` `applies systemPrompt and appendSystemPrompt behavior to the final output`。**Target:** `tests/e2e/cases/test_real_cli.py::test_applies_system_prompt_and_append_system_prompt_behavior_to_the_final_output`。`literal-port`。要求最终 JSON 同时包含 `SYS_TAG_ALPHA` 与 `APPEND_TAG_BETA` 两个标记。
12. **Source:** `real-cli.test.ts:321` `reads system prompts from files and can access cwd/additionalDirectories`。**Target:** `tests/e2e/cases/test_real_cli.py::test_reads_system_prompts_from_files_and_can_access_cwd_and_additional_directories`。`literal-port`。要求模型能读到 `cwd` 和 `additionalDirectories` 中的 probe 文件，并体现 `systemPromptFile` / `appendSystemPromptFile` 的提示词效果。
13. **Source:** `real-cli.test.ts:372` `records tool restrictions, debug files, settings and plugin directory in the real spawn args`。**Target:** `tests/e2e/cases/test_real_cli.py::test_records_tool_restrictions_debug_files_settings_and_plugin_directory_in_the_real_spawn_args`。`literal-port`。要求真实 CLI 的 spawn args 中完整出现 `allowedTools`、`disallowedTools`、`tools`、`settings`、`pluginDir`、`debug`、`debugFile`、`betas`、`name` 等 flag，并生成非空 debug file。
14. **Source:** `real-cli.test.ts:433` `uses configured agent identity and noSessionPersistence blocks implicit reuse`。**Target:** `tests/e2e/cases/test_real_cli.py::test_uses_configured_agent_identity_and_no_session_persistence_blocks_implicit_reuse`。`literal-port`。要求 agent 角色配置能影响最终回答，同时 `noSessionPersistence=True` 的第二次执行不能隐式继承前一轮记忆。
