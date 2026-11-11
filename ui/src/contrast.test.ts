import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = readFileSync('src/styles.css', 'utf8');

function palette(block: string): Record<string, string> {
  return Object.fromEntries([...block.matchAll(/--([a-z-]+):\s*(#[0-9a-f]{6});/g)].map((m) => [m[1], m[2]]));
}
function luminance(hex: string): number {
  const channel = (i: number) => {
    const v = parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2);
}
function ratio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return ((hi ?? 0) + 0.05) / ((lo ?? 0) + 0.05);
}

const light = palette(css.slice(css.indexOf(':root'), css.indexOf('@media')));
const dark = palette(css.slice(css.indexOf('@media'), css.indexOf('* {')));

// [foreground, background, minimum ratio]: WCAG AA is 4.5 for text, 3 for UI components.
const PAIRS: Array<[string, string, number]> = [
  ['text', 'bg', 4.5], ['text', 'surface', 4.5], ['muted', 'bg', 4.5], ['muted', 'surface', 4.5],
  ['link', 'bg', 4.5], ['link', 'surface', 4.5], ['danger', 'bg', 4.5],
  ['button-text', 'button-bg', 4.5], ['focus', 'bg', 3], ['bar', 'surface', 3], ['border', 'bg', 3],
];

describe.each([['light', light], ['dark', dark]] as const)('%s palette meets WCAG AA contrast', (_n, colors) => {
  it.each(PAIRS)('%s on %s >= %f', (fg, bg, min) => {
    expect(colors[fg], fg).toBeDefined();
    expect(ratio(colors[fg] as string, colors[bg] as string)).toBeGreaterThanOrEqual(min);
  });
});
