import type { Config } from "tailwindcss";

/**
 * Palette notes
 * -------------
 * This is an engineering tool, not a marketing page. The rules the theme
 * enforces:
 *   - Exactly one accent hue (blue). Everything else is neutral or a severity
 *     colour, so colour always MEANS something.
 *   - Four severity colours, used only for severity. Never decoratively.
 *   - No gradients. Depth comes from 1px borders and a two-step surface scale.
 *
 * Colours are declared here rather than as CSS custom properties because they
 * are also consumed by recharts and prism-react-renderer, both of which want
 * literal strings rather than `var(--x)`.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Surfaces: page -> panel -> raised.
        base: "#08090b",
        panel: "#0f1115",
        raised: "#161a20",
        line: "#232830",
        "line-strong": "#333a45",

        // Text.
        ink: "#e8eaed",
        "ink-dim": "#a2a9b4",
        "ink-faint": "#6c7480",

        // The single accent.
        accent: "#5b9dff",
        "accent-dim": "#2f5fa8",

        // Severity scale — semantic only.
        critical: "#f2555a",
        high: "#f0883e",
        medium: "#d9b23c",
        low: "#7f8794",

        // Outcome colours for the coverage strip / reconciliation.
        ok: "#3fb950",
        warn: "#d9b23c",
        bad: "#f2555a",
      },
      fontFamily: {
        sans: [
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Inter",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "Liberation Mono",
          "monospace",
        ],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      maxWidth: {
        screen: "1600px",
      },
    },
  },
  plugins: [],
};

export default config;
