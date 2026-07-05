import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#1f2430",
        panel: "#f7f8fb",
        accent: "#2f7df6",
      },
    },
  },
  plugins: [],
} satisfies Config;
