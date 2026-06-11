/**
 * 允许 React 内联 style 里写 CSS 自定义属性（--xxx）。
 *
 * @types/react 的 CSSProperties 默认不含自定义属性索引；用声明合并补上
 * 模板字面量索引，避免 `as React.CSSProperties` 断言。
 */

import "react";

declare module "react" {
  // 声明合并只能往既有 interface 里加成员，Record 无法参与合并 → 必须用索引签名
  // eslint-disable-next-line @typescript-eslint/consistent-indexed-object-style
  interface CSSProperties {
    [key: `--${string}`]: string | number | undefined;
  }
}
