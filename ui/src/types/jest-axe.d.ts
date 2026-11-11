declare module 'jest-axe' {
  export function axe(html: Element | string, options?: unknown): Promise<unknown>;
  export const toHaveNoViolations: { toHaveNoViolations(results: unknown): unknown };
}
interface AxeMatchers<R = unknown> {
  toHaveNoViolations(): R;
}
declare module 'vitest' {
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type, @typescript-eslint/no-explicit-any
  interface Assertion<T = any> extends AxeMatchers<T> {}
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type
  interface AsymmetricMatchersContaining extends AxeMatchers {}
}
