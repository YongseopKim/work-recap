// scheduler.js — Scheduler tab: status, jobs, history, manual trigger

import { api } from "./api.js";

export function schedulerComponent() {
  return {
    state: "loading",
    jobs: [],
    history: [],
    triggerBusy: false,
    triggerResult: "",

    async init() {
      await this.refresh();
    },

    async refresh() {
      try {
        const [statusResp, historyResp] = await Promise.all([
          api("GET", "/scheduler/status"),
          api("GET", "/scheduler/history?limit=20"),
        ]);
        const status = await statusResp.json();
        const historyData = await historyResp.json();
        this.state = status.state;
        this.jobs = status.jobs;
        this.history = historyData.reverse();
      } catch (e) {
        this.state = "error";
      }
    },

    async togglePause() {
      const action = this.state === "paused" ? "resume" : "pause";
      await api("PUT", `/scheduler/${action}`);
      await this.refresh();
    },

    async triggerJob(jobName) {
      this.triggerBusy = true;
      this.triggerResult = "";
      try {
        await api("POST", `/scheduler/trigger/${jobName}`);
        this.triggerResult = `${jobName} triggered`;
        setTimeout(() => this.refresh(), 2000);
      } catch (e) {
        this.triggerResult = `Error: ${e.message}`;
      } finally {
        this.triggerBusy = false;
      }
    },

    formatTime(iso) {
      if (!iso) return "-";
      return new Date(iso).toLocaleString("ko-KR", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
    },
  };
}
