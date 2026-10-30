/**
 * Tailwind 只是工具类层：**所有颜色 / 字体 / 间距 / 圆角都指向 CSS 变量**
 * （定义在 `src/styles/tokens.css`，来自需求文档 §9.3）。
 * 这里不写任何十六进制色值，改主题只改 tokens.css 一处。
 */
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: "var(--surface)",
        border: "var(--border)",
        text: "var(--text)",
        "text-muted": "var(--text-muted)",
        primary: "var(--primary)",
        accent: "var(--accent)",
        danger: "var(--danger)",
      },
      fontFamily: {
        title: "var(--font-title)",
        body: "var(--font-body)",
        mono: "var(--font-mono)",
      },
      borderRadius: {
        control: "var(--radius-control)",
        card: "var(--radius-card)",
        pill: "var(--radius-pill)",
      },
      spacing: {
        1: "var(--space-1)",
        2: "var(--space-2)",
        3: "var(--space-3)",
        4: "var(--space-4)",
        6: "var(--space-6)",
        8: "var(--space-8)",
        12: "var(--space-12)",
        16: "var(--space-16)",
      },
      maxWidth: {
        container: "var(--container-max)",
        prose: "var(--prose-width)",
      },
      boxShadow: {
        hairline: "var(--shadow-1)",
      },
      transitionDuration: {
        fast: "150ms",
      },
    },
  },
  plugins: [],
};
