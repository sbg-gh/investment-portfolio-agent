import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import jsxA11y from 'eslint-plugin-jsx-a11y';

const noHtmlInjection = {
  selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
  message: 'dangerouslySetInnerHTML is banned: all server and model text renders as inert text.',
};
const noInnerHtml = {
  selector: "AssignmentExpression[left.property.name=/^(innerHTML|outerHTML)$/]",
  message: 'innerHTML/outerHTML assignment is banned.',
};

export default tseslint.config(
  { ignores: ['node_modules', '../src/portfolio_agent/web/static', 'dist'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  jsxA11y.flatConfigs.recommended,
  {
    files: ['e2e/**/*.mjs'],
    languageOptions: { globals: { process: 'readonly', console: 'readonly', fetch: 'readonly' } },
  },
  {
    files: ['**/*.{ts,tsx}'],
    rules: { 'no-restricted-syntax': ['error', noHtmlInjection, noInnerHtml] },
  },
  {
    // Money is decimal-string BigInt math only: no floats, no Number coercion, no Math.
    files: ['src/money/**/*.ts'],
    ignores: ['src/money/**/*.test.ts'],
    rules: {
      'no-restricted-globals': [
        'error',
        { name: 'parseFloat', message: 'No float math on money.' },
        { name: 'parseInt', message: 'No float math on money.' },
        { name: 'Number', message: 'No Number coercion on money.' },
        { name: 'Math', message: 'No Math on money.' },
      ],
      'no-restricted-syntax': [
        'error',
        noHtmlInjection,
        noInnerHtml,
        { selector: "Literal[raw=/^[0-9]*\\.[0-9]+$/]", message: 'No float literals in money code.' },
      ],
    },
  },
);
