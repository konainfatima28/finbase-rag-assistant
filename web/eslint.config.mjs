import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    // Explicit version: eslint-plugin-react auto-detection uses an API removed in ESLint 10.
    settings: { react: { version: "19.3" } },
    rules: {
      // Model output must never be injected as HTML (XSS); enforced project-wide.
      "react/no-danger": "error",
    },
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts", "coverage/**", "playwright-report/**", "test-results/**"]),
]);
