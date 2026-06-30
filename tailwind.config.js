/** Tailwind config for the built stylesheet (replaces the CDN).
 *  Build with `just build-css`; the output static/tailwind.css is committed so
 *  the booth works offline (no CDN / internet needed).
 *
 *  content must include the Python sources: the tile/keyboard feedback classes
 *  (bg-green-500, bg-wordle-absent, ...) live in rendering.py, not the templates.
 */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./static/**/*.js",
    "./src/durable_wordle/**/*.py",
  ],
  theme: {
    extend: {
      colors: {
        "temporal-uv": "#444CE7",
        "temporal-black": "#141414",
        "temporal-white": "#F8FAFC",
        "temporal-grellow": "#cfff0d",
        "temporal-indigo": "#cacbf9",
        "wordle-absent": "#2d3458",
        "wordle-tile-empty": "#243349",
        "wordle-tile-active": "#374761",
        "wordle-key": "#7c8fb1",
      },
      fontFamily: {
        mono: ['"Space Mono"', "monospace"],
      },
    },
  },
};
