import js from '@eslint/js'
import globals from 'globals'
import importPlugin from 'eslint-plugin-import'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import unicorn from 'eslint-plugin-unicorn'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      ...tseslint.configs.strictTypeChecked,
      ...tseslint.configs.stylisticTypeChecked,
      importPlugin.flatConfigs.recommended,
      importPlugin.flatConfigs.typescript,
      react.configs.flat.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
      unicorn.configs['flat/recommended'],
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        project: ['./tsconfig.app.json', './tsconfig.node.json'],
        tsconfigRootDir: import.meta.dirname,
      },
    },
    settings: {
      react: {
        version: 'detect',
      },
      'import/resolver': {
        typescript: {
          project: ['./tsconfig.app.json', './tsconfig.node.json'],
        },
      },
    },
    rules: {
      // React 17+ 新 JSX Transform：不需要手动 import React
      'react/react-in-jsx-scope': 'off',

      // 文件名约束不适用于本项目（组件通常是 PascalCase）
      'unicorn/filename-case': 'off',

      // 本项目强制：不要 any
      '@typescript-eslint/no-explicit-any': 'error',

      // TS 严格：Promise 必须处理
      '@typescript-eslint/no-floating-promises': 'error',

      // 避免误用 ||，优先 ??
      '@typescript-eslint/prefer-nullish-coalescing': 'error',

      // 允许测试中临时使用 console
      'no-console': 'off',

      // Vite/react-refresh 常见写法
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],

      // 允许 _ 前缀的未使用参数（常用于占位）
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],

      // 关闭过度严格的 unicorn 规则，避免影响可读性
      'unicorn/prevent-abbreviations': 'off',
    },
  },
])
