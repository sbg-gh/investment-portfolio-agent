import { ESLint } from 'eslint';
import { describe, expect, it } from 'vitest';

async function lint(code: string, filePath: string) {
  const eslint = new ESLint({ cwd: process.cwd() });
  const [result] = await eslint.lintText(code, { filePath });
  return (result?.messages ?? []).map((m) => `${m.ruleId}: ${m.message}`);
}

describe('money and markup lint bans', () => {
  it.each(['parseFloat("1.5")', 'Number("1")', 'Math.round(1n as never)', 'parseInt("1")'])(
    'rejects %s inside src/money',
    async (expr) => {
      const messages = await lint(`export const x = ${expr};\n`, 'src/money/bad.ts');
      expect(messages.join('\n')).toMatch(/no-restricted-globals/);
    },
  );
  it('rejects float literals inside src/money', async () => {
    const messages = await lint('export const x = 0.5;\n', 'src/money/bad.ts');
    expect(messages.join('\n')).toMatch(/float literals/);
  });
  it('rejects dangerouslySetInnerHTML everywhere, including money files', async () => {
    const code = 'export const A = () => <div dangerouslySetInnerHTML={{ __html: "x" }} />;\n';
    expect((await lint(code, 'src/screens/Bad.tsx')).join('\n')).toMatch(/dangerouslySetInnerHTML/);
  });
  it('rejects innerHTML assignment', async () => {
    const messages = await lint('document.body.innerHTML = "x";\n', 'src/bad.ts');
    expect(messages.join('\n')).toMatch(/innerHTML/);
  });
  it('allows Number outside src/money', async () => {
    expect(await lint('export const x = Number("1");\n', 'src/other.ts')).toEqual([]);
  });
});
