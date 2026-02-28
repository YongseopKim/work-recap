// summaries.js — Summaries tab Alpine component with calendar view

import { api, copyToClipboard } from "./api.js";

/**
 * Build calendar grid weeks for a given year/month.
 * Returns array of weeks, each week is array of 7 day objects.
 * Days outside the month have `outside: true`.
 */
function buildCalendarDays(year, month) {
  // month is 1-based
  const firstDay = new Date(year, month - 1, 1);
  const lastDay = new Date(year, month, 0); // last day of month
  const daysInMonth = lastDay.getDate();

  // Monday=0 .. Sunday=6 (ISO week)
  let startDow = firstDay.getDay(); // 0=Sun..6=Sat
  startDow = startDow === 0 ? 6 : startDow - 1; // convert to Mon=0..Sun=6

  const cells = [];

  // Fill leading days from previous month
  if (startDow > 0) {
    const prevLastDay = new Date(year, month - 1, 0).getDate();
    for (let i = startDow - 1; i >= 0; i--) {
      cells.push({
        day: prevLastDay - i,
        date: null,
        outside: true,
        available: false,
      });
    }
  }

  // Days of current month
  for (let d = 1; d <= daysInMonth; d++) {
    const mm = String(month).padStart(2, "0");
    const dd = String(d).padStart(2, "0");
    cells.push({
      day: d,
      date: `${year}-${mm}-${dd}`,
      dateKey: `${mm}-${dd}`, // matches API response format
      outside: false,
      available: false,
    });
  }

  // Fill trailing days to complete last week
  const remaining = cells.length % 7;
  if (remaining > 0) {
    for (let d = 1; d <= 7 - remaining; d++) {
      cells.push({
        day: d,
        date: null,
        outside: true,
        available: false,
      });
    }
  }

  // Split into weeks
  const weeks = [];
  for (let i = 0; i < cells.length; i += 7) {
    weeks.push(cells.slice(i, i + 7));
  }
  return weeks;
}

/**
 * Get ISO week number for a date string (YYYY-MM-DD).
 */
function getISOWeek(dateStr) {
  const d = new Date(dateStr + "T00:00:00");
  d.setDate(d.getDate() + 3 - ((d.getDay() + 6) % 7));
  const yearStart = new Date(d.getFullYear(), 0, 4);
  yearStart.setDate(yearStart.getDate() + 3 - ((yearStart.getDay() + 6) % 7));
  return Math.round((d - yearStart) / 86400000 / 7) + 1;
}

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

export function summariesComponent() {
  return {
    // Calendar state
    calYear: new Date().getFullYear(),
    calMonth: new Date().getMonth() + 1, // 1-based
    weeks: [],
    availableDaily: [],
    availableWeekly: [],
    availableMonthly: [],
    hasYearly: false,
    loading: false,

    // Summary viewer
    summaryContent: "",
    summaryRaw: "",
    summaryLabel: "",
    showSummary: false,
    summaryError: "",
    copyOk: false,
    _currentDate: null,  // track currently viewed daily summary date

    get monthName() {
      return MONTH_NAMES[this.calMonth - 1];
    },

    init() {
      this.loadAvailable();
    },

    prevMonth() {
      if (this.calMonth === 1) {
        this.calMonth = 12;
        this.calYear--;
      } else {
        this.calMonth--;
      }
      this.loadAvailable();
    },

    nextMonth() {
      if (this.calMonth === 12) {
        this.calMonth = 1;
        this.calYear++;
      } else {
        this.calMonth++;
      }
      this.loadAvailable();
    },

    async loadAvailable() {
      this.loading = true;
      this.summaryError = "";
      try {
        const resp = await api(
          "GET",
          `/summaries/available?year=${this.calYear}&month=${this.calMonth}`
        );
        const data = await resp.json();
        this.availableDaily = data.daily || [];   // ["02-01", "02-03", ...]
        this.availableWeekly = data.weekly || [];  // ["W05", "W06", ...]
        this.availableMonthly = data.monthly || []; // ["02"]
        this.hasYearly = data.yearly || false;
      } catch (e) {
        this.summaryError = "Failed to load available summaries.";
        this.availableDaily = [];
        this.availableWeekly = [];
        this.availableMonthly = [];
        this.hasYearly = false;
      }

      // Rebuild calendar grid with availability
      this.weeks = buildCalendarDays(this.calYear, this.calMonth);
      const dailySet = new Set(this.availableDaily);
      for (const week of this.weeks) {
        for (const cell of week) {
          if (!cell.outside && cell.dateKey) {
            cell.available = dailySet.has(cell.dateKey);
          }
        }
      }
      this.loading = false;
    },

    async loadDailySummary(date) {
      this.showSummary = false;
      this.summaryError = "";
      try {
        const resp = await api("GET", `/summary/daily/${date}`);
        if (resp.status === 404) {
          this.summaryError = "Daily summary not found. Run the pipeline first.";
          return;
        }
        const data = await resp.json();
        this.summaryRaw = data.content;
        this.summaryContent = window.marked.parse(data.content);
        this.summaryLabel = `Daily: ${date}`;
        this.showSummary = true;
        this.copyOk = false;

        // Compute navigation links
        this._currentDate = date;
      } catch (e) {
        this.summaryError = e.message;
      }
    },

    async loadWeeklySummary(weekStr) {
      // weekStr like "W06"
      const weekNum = parseInt(weekStr.slice(1), 10);
      this.showSummary = false;
      this.summaryError = "";
      try {
        const resp = await api(
          "GET",
          `/summary/weekly/${this.calYear}/${weekNum}`
        );
        if (resp.status === 404) {
          this.summaryError = "Weekly summary not found.";
          return;
        }
        const data = await resp.json();
        this.summaryRaw = data.content;
        this.summaryContent = window.marked.parse(data.content);
        this.summaryLabel = `Weekly: ${this.calYear} ${weekStr}`;
        this.showSummary = true;
        this.copyOk = false;
        this._currentDate = null;
      } catch (e) {
        this.summaryError = e.message;
      }
    },

    async loadMonthlySummary(monthStr) {
      // monthStr like "02"
      const monthNum = parseInt(monthStr, 10);
      this.showSummary = false;
      this.summaryError = "";
      try {
        const resp = await api(
          "GET",
          `/summary/monthly/${this.calYear}/${monthNum}`
        );
        if (resp.status === 404) {
          this.summaryError = "Monthly summary not found.";
          return;
        }
        const data = await resp.json();
        this.summaryRaw = data.content;
        this.summaryContent = window.marked.parse(data.content);
        this.summaryLabel = `Monthly: ${this.calYear}-${monthStr}`;
        this.showSummary = true;
        this.copyOk = false;
        this._currentDate = null;
      } catch (e) {
        this.summaryError = e.message;
      }
    },

    async loadYearlySummary() {
      this.showSummary = false;
      this.summaryError = "";
      try {
        const resp = await api("GET", `/summary/yearly/${this.calYear}`);
        if (resp.status === 404) {
          this.summaryError = "Yearly summary not found.";
          return;
        }
        const data = await resp.json();
        this.summaryRaw = data.content;
        this.summaryContent = window.marked.parse(data.content);
        this.summaryLabel = `Yearly: ${this.calYear}`;
        this.showSummary = true;
        this.copyOk = false;
        this._currentDate = null;
      } catch (e) {
        this.summaryError = e.message;
      }
    },

    /** Get the ISO week string for the currently viewed daily summary. */
    get currentWeekStr() {
      if (!this._currentDate) return null;
      const w = getISOWeek(this._currentDate);
      return `W${String(w).padStart(2, "0")}`;
    },

    /** Get the month string for the currently viewed daily summary. */
    get currentMonthStr() {
      if (!this._currentDate) return null;
      return this._currentDate.slice(5, 7);
    },

    /** Navigate from daily to weekly summary. */
    viewWeekFromDaily() {
      const ws = this.currentWeekStr;
      if (ws) this.loadWeeklySummary(ws);
    },

    /** Navigate from daily to monthly summary. */
    viewMonthFromDaily() {
      const ms = this.currentMonthStr;
      if (ms) this.loadMonthlySummary(ms);
    },

    async copySummary() {
      const ok = await copyToClipboard(this.summaryRaw);
      if (ok) {
        this.copyOk = true;
        setTimeout(() => (this.copyOk = false), 2000);
      }
    },

    onDayClick(cell) {
      if (cell.outside || !cell.date) return;
      if (cell.available) {
        this.loadDailySummary(cell.date);
      }
    },
  };
}
