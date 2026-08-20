# Agent 工作规则

本文件用于约束本仓库中的 AI Agent / 协作开发行为。

## 提交规则

1. **每完成一个可独立提交的步骤，必须立即执行 GitHub 提交**，不要等到最后一次性提交。
2. 提交前先 `git add` 相关文件，再 `git commit`。
3. 提交信息使用 Conventional Commits 格式，例如：
   - `feat(dashboard): add PageSectionNav component`
   - `fix(llm): remove schedule fallback`
   - `docs: add agent commit rules`
   - `refactor(provider): normalize api_base endpoints`
4. 每个提交应保持“小而完整”：一个提交只做一件事，便于回滚和审查。
5. 如果当前工作区有未提交的无关改动，不要混入当前提交；只提交与当前步骤相关的文件。
