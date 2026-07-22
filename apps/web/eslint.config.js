import js from "@eslint/js";
import vue from "eslint-plugin-vue";
import tseslint from "typescript-eslint";

export default tseslint.config(
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...vue.configs["flat/essential"],
  {
    ignores: ["dist/**", "node_modules/**", "**/*.vue.js"],
  },
  {
    files: ["**/*.vue"],
    languageOptions: {
      parserOptions: { parser: tseslint.parser },
      globals: {
        window: "readonly",
        document: "readonly",
        localStorage: "readonly",
        prompt: "readonly",
        clearInterval: "readonly",
        URL: "readonly",
        Event: "readonly",
        HTMLInputElement: "readonly",
      },
    },
    rules: { "vue/multi-word-component-names": "off" },
  },
);
