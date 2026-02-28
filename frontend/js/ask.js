// ask.js — Ask tab Alpine component with chat history

import { api, streamJob, copyToClipboard, escapeHtml } from "./api.js";

const QUICK_QUESTIONS = [
  "What were my key achievements this week?",
  "Summarize my most impactful PRs this month.",
  "What areas have I been focusing on recently?",
];

export function askComponent() {
  return {
    question: "",
    months: 3,
    messages: [],     // { role: "user"|"assistant", text: string, html?: string, copyOk: bool }
    busy: false,
    quickQuestions: QUICK_QUESTIONS,

    askQuestion(q = null) {
      const text = (q || this.question).trim();
      if (!text) {
        alert("Please enter a question.");
        return;
      }

      // Add user message
      this.messages.push({ role: "user", text, html: null, copyOk: false });
      this.question = "";
      this.busy = true;

      // Add placeholder assistant message
      const assistantIdx = this.messages.length;
      this.messages.push({
        role: "assistant",
        text: "",
        html: "<em>Thinking...</em>",
        copyOk: false,
        loading: true,
      });

      // Scroll to bottom
      this.$nextTick(() => this.scrollToBottom());

      api("POST", "/query", { question: text, months: this.months })
        .then((resp) => resp.json())
        .then(({ job_id }) => {
          streamJob(job_id, (job) => {
            if (job.status === "completed" && job.result) {
              this.messages[assistantIdx] = {
                role: "assistant",
                text: job.result,
                html: window.marked.parse(job.result),
                copyOk: false,
                loading: false,
              };
              this.busy = false;
              this.$nextTick(() => this.scrollToBottom());
            } else if (job.status === "failed") {
              const safeError = escapeHtml(job.error || "Unknown error");
              this.messages[assistantIdx] = {
                role: "assistant",
                text: `Error: ${job.error || "Unknown error"}`,
                html: `<span class="status-failed">Error: ${safeError}</span>`,
                copyOk: false,
                loading: false,
              };
              this.busy = false;
              this.$nextTick(() => this.scrollToBottom());
            }
          });
        })
        .catch((e) => {
          const safeError = escapeHtml(e.message);
          this.messages[assistantIdx] = {
            role: "assistant",
            text: `Error: ${e.message}`,
            html: `<span class="status-failed">Error: ${safeError}</span>`,
            copyOk: false,
            loading: false,
          };
          this.busy = false;
        });
    },

    async copyMessage(idx) {
      const msg = this.messages[idx];
      if (!msg) return;
      const ok = await copyToClipboard(msg.text);
      if (ok) {
        this.messages[idx].copyOk = true;
        setTimeout(() => {
          this.messages[idx].copyOk = false;
        }, 2000);
      }
    },

    scrollToBottom() {
      const el = this.$refs.chatContainer;
      if (el) el.scrollTop = el.scrollHeight;
    },

    onKeydown(e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        this.askQuestion();
      }
    },
  };
}
