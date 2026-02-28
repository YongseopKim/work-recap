// pipeline.js — Pipeline tab Alpine component

import { api, streamJob, escapeHtml, renderJobStatus, todayStr, getISOWeek, getISOWeekYear } from "./api.js";

export function pipelineComponent() {
  return {
    // Single date run
    singleDate: todayStr(),
    // Range run
    rangeFrom: "",
    rangeTo: todayStr(),
    // Options
    force: false,
    workers: 5,
    enrich: true,
    batch: false,
    weeklySum: false,
    monthlySum: false,
    yearlySum: false,
    // Status
    busy: false,
    rangeBusy: false,
    showStatus: false,
    statusHtml: "",
    completedDate: null,

    runSingle() {
      if (!this.singleDate) {
        alert("Please select a date.");
        return;
      }
      this.busy = true;
      this.showStatus = true;
      this.completedDate = null;
      this.statusHtml = renderJobStatus({ status: "accepted", job_id: "..." });

      api("POST", `/pipeline/run/${this.singleDate}`, {
        force: this.force,
        enrich: this.enrich,
      })
        .then((resp) => resp.json())
        .then(({ job_id }) => {
          streamJob(job_id, (job) => {
            this.statusHtml = renderJobStatus(job);
            if (job.status === "completed") {
              this.completedDate = this.singleDate;
            }
            if (job.status === "completed" || job.status === "failed") {
              this.busy = false;
            }
          });
        })
        .catch((e) => {
          this.statusHtml = `<span class="status-failed">${escapeHtml(e.message)}</span>`;
          this.busy = false;
        });
    },

    runRange() {
      if (!this.rangeFrom || !this.rangeTo) {
        alert("Please select both dates.");
        return;
      }
      this.rangeBusy = true;
      this.showStatus = true;
      this.completedDate = null;
      this.statusHtml = renderJobStatus({ status: "accepted", job_id: "..." });

      // Derive hierarchical summarization values from until date
      const untilDate = new Date(this.rangeTo + "T00:00:00");
      const isoWeek = getISOWeek(this.rangeTo);
      const isoWeekYear = getISOWeekYear(this.rangeTo);

      api("POST", "/pipeline/run/range", {
        since: this.rangeFrom,
        until: this.rangeTo,
        force: this.force,
        max_workers: this.workers,
        enrich: this.enrich,
        batch: this.batch,
        summarize_weekly: this.weeklySum
          ? `${isoWeekYear}-${isoWeek}`
          : null,
        summarize_monthly: this.monthlySum
          ? `${untilDate.getFullYear()}-${untilDate.getMonth() + 1}`
          : null,
        summarize_yearly: this.yearlySum
          ? untilDate.getFullYear()
          : null,
      })
        .then((resp) => resp.json())
        .then(({ job_id }) => {
          streamJob(job_id, (job) => {
            this.statusHtml = renderJobStatus(job);
            if (job.status === "completed" || job.status === "failed") {
              this.rangeBusy = false;
            }
          });
        })
        .catch((e) => {
          this.statusHtml = `<span class="status-failed">${escapeHtml(e.message)}</span>`;
          this.rangeBusy = false;
        });
    },

    viewCompletedSummary() {
      // Dispatch custom event to switch to summaries tab and load the date
      this.$dispatch("view-summary", { date: this.completedDate });
    },
  };
}
