<!--
Copyright 2026 @lyty1997

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# 前端技术栈

## 1. 核心技术

- **构建工具**：Vite 8
- **框架**：React 19
- **语言**：TypeScript 5.9（strict mode）
- **样式**：CSS Modules
- **Markdown 渲染**：react-markdown 10 + remark-gfm + rehype-raw
- **数据校验**：zod v4
- **i18n**：自建 Context（`src/i18n/`），支持 zh-CN / zh-TW / en 三语

## 2. TypeScript 配置

必须启用的编译选项：
- `strict: true`
- `noUncheckedIndexedAccess: true`
- `exactOptionalPropertyTypes: true`
- `noImplicitOverride: true`
- `noFallthroughCasesInSwitch: true`
- `forceConsistentCasingInFileNames: true`
- `verbatimModuleSyntax: true`
- `noPropertyAccessFromIndexSignature: true`

## 3. 代码质量规范

### 3.1 类型安全
- 禁止 `any`（用 `unknown` + 收窄）
- 禁止 `@ts-ignore`（用 `@ts-expect-error` + 注释）
- 禁止 `as` 断言（用 type guard / zod；`as const` 和测试 mock 除外）
- 函数返回值必须显式标注

### 3.2 运行时校验
- 外部输入（API 响应）必须 zod schema 校验
- 类型单源派生：`type Foo = z.infer<typeof FooSchema>`
- 环境变量必须走统一校验层

### 3.3 错误处理
- 禁止空 `catch {}`
- `catch(err: unknown)` 必须收窄后处理
- 业务错误继承 `AppError` 基类

### 3.4 资源清理
- 事件监听器 / `setInterval` / `setTimeout` 必须在组件卸载时清理
- WebSocket 连接必须在组件卸载时关闭
- 可取消异步操作用 `AbortController`

## 4. ESLint 规则

启用规则集：
- `@typescript-eslint/strict-type-checked`
- `@typescript-eslint/stylistic-type-checked`
- `eslint-plugin-unicorn`（推荐规则）

关键规则：
- `no-floating-promises`
- `no-misused-promises`
- `restrict-template-expressions`
- `no-unnecessary-condition`
- `prefer-nullish-coalescing`
- `switch-exhaustiveness-check`

## 5. 项目结构

```
frontend/
├── src/
│   ├── api/              # API 客户端与 zod schema
│   ├── components/       # UI 组件（SourcePicker / TaskForm / TaskDetail / TaskResult ...）
│   ├── features/task/    # 任务领域模块（状态管理、数据流）
│   ├── hooks/            # 自定义 Hook（useTheme 等）
│   ├── i18n/             # i18n Context + 语言包（zh-CN / zh-TW / en）
│   ├── App.tsx           # 根组件
│   └── main.tsx          # 入口
├── tests/                # vitest 单元测试（jsdom 环境）
├── public/               # 静态资源
└── vite.config.ts        # Vite 配置（dev proxy：/api → 127.0.0.1:8000）
```

## 6. 开发工具

### 6.1 启动命令
```bash
npm run dev      # 开发服务器
npm run build    # 生产构建
npm run preview  # 预览构建结果
npm run lint     # ESLint 检查
npm run test     # 运行测试
```

### 6.2 Vite Proxy
开发模式下，`/api` 请求代理到 `127.0.0.1:8000`（含 WebSocket）。

## 7. React 19 注意事项

- `FormEvent` 已废弃，使用 `action` 属性或原生事件类型
- `useTransition` / `useOptimistic` 等新 Hook 可选使用
- Server Components 暂不使用（纯 SPA）

## 8. 测试

- 框架：vitest 4（jsdom 环境）
- 配套：`@testing-library/react` + `@testing-library/user-event` + `@testing-library/jest-dom`
- 测试目录：`frontend/tests/`，镜像 `src/` 结构
- 覆盖率：核心功能 ≥ 80%

## 9. 相关文档

- [功能设计](features.md)
- [后端 API](../backend/api.md)
