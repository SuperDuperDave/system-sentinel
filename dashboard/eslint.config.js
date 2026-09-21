// What the dashboard is checked for beyond types. `tsc --noEmit` says whether the code means
// anything; this says whether it behaves: a hook that lies about what it depends on, a value read
// where React has not settled it, a control only a mouse can reach.
//
// eslint 9 rather than 10 because eslint-plugin-jsx-a11y peers at 9, and an accessibility rule set
// that actually runs is worth more than the newer major.

import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', '../sentinel/static'] },
  {
    files: ['src/**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      ...tseslint.configs.recommended,
      reactHooks.configs.flat['recommended-latest'],
      jsxA11y.flatConfigs.recommended,
    ],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    rules: {
      // eslint-plugin-react-hooks 7 carries the React Compiler's own advisories beside the two
      // rules everyone means by "the hooks rules". This dashboard has not adopted the compiler, and
      // both of these reported only code that is correct without it: an effect that starts a
      // request and marks it in flight (useReading, Devices, Stack, App), and a ref object handed
      // to a child as `ref=`, which is not reading `.current` during render (Record, Devices).
      // Off here with the reason, rather than a disable comment at each of the nine sites.
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/refs': 'off',

      // A region that scrolls has to be reachable by keyboard, which is the opposite of what this
      // rule assumes about a non-interactive element with a tabIndex. Allowed where the element
      // says what it is: the composed handoff is a labelled region a reader can scroll.
      'jsx-a11y/no-noninteractive-tabindex': ['error', { tags: [], roles: ['tabpanel', 'region'], allowExpressionValues: true }],
    },
  },
);
