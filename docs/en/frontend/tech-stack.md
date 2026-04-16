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

# Frontend Tech Stack

## 1. Core Technologies

- **Build tool**: Vite 8
- **Framework**: React 19
- **Language**: TypeScript 5.9 (strict mode)
- **Styling**: CSS Modules
- **Markdown rendering**: react-markdown 10 + remark-gfm + rehype-raw
- **Data validation**: zod v4
- **i18n**: Custom Context (`src/i18n/`), supports zh-CN / zh-TW / en

## 2. TypeScript Configuration

Required compiler options:
- `strict: true`
- `noUncheckedIndexedAccess: true`
- `exactOptionalPropertyTypes: true`
- `noImplicitOverride: true`
- `noFallthroughCasesInSwitch: true`
- `forceConsistentCasingInFileNames: true`
- `verbatimModuleSyntax: true`
- `noPropertyAccessFromIndexSignature: true`

## 3. Code Quality Standards

### 3.1 Type Safety
- `any` is forbidden (use `unknown` + narrowing)
- `@ts-ignore` is forbidden (use `@ts-expect-error` + comment)
- `as` assertions are forbidden (use type guards / zod; `as const` and test mocks are exceptions)
- Function return types must be explicitly annotated

### 3.2 Runtime Validation
- External inputs (API responses) must be validated with zod schemas
- Types are derived from a single source: `type Foo = z.infer<typeof FooSchema>`
- Environment variables must go through a unified validation layer

### 3.3 Error Handling
- Empty `catch {}` blocks are forbidden
- `catch(err: unknown)` must narrow the type before handling
- Business errors inherit from the `AppError` base class

### 3.4 Resource Cleanup
- Event listeners / `setInterval` / `setTimeout` must be cleaned up on component unmount
- WebSocket connections must be closed on component unmount
- Cancellable async operations use `AbortController`

## 4. ESLint Rules

Enabled rule sets:
- `@typescript-eslint/strict-type-checked`
- `@typescript-eslint/stylistic-type-checked`
- `eslint-plugin-unicorn` (recommended rules)

Key rules:
- `no-floating-promises`
- `no-misused-promises`
- `restrict-template-expressions`
- `no-unnecessary-condition`
- `prefer-nullish-coalescing`
- `switch-exhaustiveness-check`

## 5. Project Structure

```
frontend/
├── src/
│   ├── api/              # API client and zod schemas
│   ├── components/       # UI components (SourcePicker / TaskForm / TaskDetail / TaskResult ...)
│   ├── features/task/    # Task domain modules (state management, data flow)
│   ├── hooks/            # Custom Hooks (useTheme etc.)
│   ├── i18n/             # i18n Context + language packs (zh-CN / zh-TW / en)
│   ├── App.tsx           # Root component
│   └── main.tsx          # Entry point
├── tests/                # vitest unit tests (jsdom environment)
├── public/               # Static assets
└── vite.config.ts        # Vite config (dev proxy: /api → 127.0.0.1:8000)
```

## 6. Development Tools

### 6.1 Commands
```bash
npm run dev      # Development server
npm run build    # Production build
npm run preview  # Preview build output
npm run lint     # ESLint check
npm run test     # Run tests
```

### 6.2 Vite Proxy
In development mode, `/api` requests are proxied to `127.0.0.1:8000` (including WebSocket).

## 7. React 19 Considerations

- `FormEvent` is deprecated; use the `action` attribute or native event types instead
- New Hooks like `useTransition` / `useOptimistic` may be used optionally
- Server Components are not used (pure SPA)

## 8. Testing

- Framework: vitest 4 (jsdom environment)
- Companion libraries: `@testing-library/react` + `@testing-library/user-event` + `@testing-library/jest-dom`
- Test directory: `frontend/tests/`, mirrors `src/` structure
- Coverage: core features >= 80%

## 9. Related Documentation

- [Feature Design](features.md)
- [Backend API](../backend/api.md)
