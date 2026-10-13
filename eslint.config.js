// Lint configuration for the panel code. Development only; nothing here ships.
import js from "@eslint/js";
import globals from "globals";

export default [
  { ignores: ["node_modules/", ".venv/", ".tools/"] },
  js.configs.recommended,
  {
    files: ["custom_components/event_blackbox/frontend/**/*.js"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "module",
      globals: globals.browser,
    },
    rules: {
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-new-func": "error",
      "no-script-url": "error",
      "no-restricted-properties": [
        "error",
        { property: "innerHTML", message: "Render data with textContent." },
        { property: "outerHTML", message: "Render data with textContent." },
        { property: "insertAdjacentHTML", message: "Render data with textContent." },
        { object: "document", property: "write", message: "Not allowed." },
      ],
    },
  },
  {
    files: ["tests/frontend/**/*.js"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "module",
      globals: globals.node,
    },
  },
];
