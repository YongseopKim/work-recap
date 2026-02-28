// app.js — Alpine init, component registration, dark mode, tab routing

import { pipelineComponent } from "./pipeline.js";
import { summariesComponent } from "./summaries.js";
import { askComponent } from "./ask.js";

// Register Alpine components before Alpine auto-starts
document.addEventListener("alpine:init", () => {
  Alpine.data("pipeline", pipelineComponent);
  Alpine.data("summaries", summariesComponent);
  Alpine.data("ask", askComponent);

  // Global app store for tab + dark mode
  Alpine.store("app", {
    tab: "pipeline",

    darkMode: localStorage.getItem("darkMode") === "true",

    init() {
      this.applyTheme();
    },

    toggleTheme() {
      this.darkMode = !this.darkMode;
      localStorage.setItem("darkMode", this.darkMode);
      this.applyTheme();
    },

    applyTheme() {
      document.documentElement.setAttribute(
        "data-theme",
        this.darkMode ? "dark" : "light"
      );
    },
  });
});
