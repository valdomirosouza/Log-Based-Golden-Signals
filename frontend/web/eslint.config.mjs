import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import prettier from "eslint-config-prettier/flat";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Disable formatting rules that conflict with Prettier (project formats via Prettier).
  prettier,
  // Generated OpenAPI client — not hand-authored, excluded from linting
  // (ported from the legacy .eslintrc.json `ignorePatterns`).
  globalIgnores([
    "src/lib/api/**",
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
