import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx}", "./components/**/*.{js,ts,jsx,tsx}", "./lib/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        panel: "#111317",
        panel2: "#171a20",
        stroke: "#242936",
        luma: {
          bg: "#0a0a0a",
          elevated: "#0e0e0e",
        },
      },
      fontFamily: {
        sans: ["var(--font-luma-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-luma-mono)", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      spacing: {
        "luma-1": "var(--space-1)",
        "luma-2": "var(--space-2)",
        "luma-3": "var(--space-3)",
        "luma-4": "var(--space-4)",
        "luma-5": "var(--space-5)",
        "luma-6": "var(--space-6)",
      },
      boxShadow: {
        soft: "0 10px 30px rgba(0,0,0,0.35)",
      },
    },
  },
  plugins: [],
};

export default config;
