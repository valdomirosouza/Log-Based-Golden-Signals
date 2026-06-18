// Ambient declarations for CSS side-effect imports (e.g. `import "./globals.css"`).
//
// Next.js injects these types at build/dev time via `next-env.d.ts`, which is
// gitignored and NOT regenerated before the CI `tsc --noEmit` type-check step.
// Under TypeScript 6, the side-effect CSS import in src/app/layout.tsx fails with
// TS2882 when that ambient declaration is absent. This file restores the
// declaration in a runtime-independent way without weakening tsconfig strictness.
declare module "*.css";
