/**
 * vitest 全局 setup。
 *
 * jsdom 不实现 ResizeObserver，而 CodeViewer 等组件用它跟踪容器尺寸驱动
 * 行级虚拟化。这里提供一个 no-op 桩，使这些组件能在测试环境正常挂载。
 */

class ResizeObserverStub {
  // 测试桩：构造时不保存回调、不主动触发；observe/disconnect 均为 no-op。
  observe(_target: Element): void {
    // no-op
  }

  unobserve(_target: Element): void {
    // no-op
  }

  disconnect(): void {
    // no-op
  }
}

globalThis.ResizeObserver = ResizeObserverStub;
