declare module 'jest-axe' {
  export function axe(html: Element | string, options?: unknown): Promise<unknown>;
  export const toHaveNoViolations: { toHaveNoViolations(results: unknown): unknown };
}
