import type { Config } from "jest";

const config: Config = {
  testEnvironment: "jsdom",
  transform: {
    // rootDir "." pins the emit layout to the project root so TypeScript 6 does
    // not infer it from the (variable) set of compiled files under src/__tests__
    // — that inference raised TS5011 under TS6. This override only affects
    // ts-jest's in-memory compile; next build and `tsc --noEmit` use the file
    // tsconfig.json (noEmit), which is unchanged.
    "^.+\\.(ts|tsx)$": ["ts-jest", { tsconfig: { jsx: "react-jsx", rootDir: "." } }],
  },
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/src/$1",
    "\\.(css|scss|sass|less)$": "<rootDir>/jest.style-mock.js",
  },
  setupFilesAfterEnv: ["<rootDir>/jest.setup.ts"],
  testMatch: ["<rootDir>/src/__tests__/**/*.test.{ts,tsx}"],
  collectCoverageFrom: [
    "src/**/*.{ts,tsx}",
    "!src/**/*.d.ts",
    "!src/lib/api/**", // generated OpenAPI client — not hand-tested
  ],
  coverageThreshold: {
    global: { lines: 80 },
  },
};

export default config;
