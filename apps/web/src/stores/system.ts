import { defineStore } from "pinia";
import { api } from "../api";
import type { Dashboard, JobInfo, NodeInfo } from "../types";

export const useSystemStore = defineStore("system", {
  state: () => ({
    dashboard: null as Dashboard | null,
    jobs: [] as JobInfo[],
    nodes: [] as NodeInfo[],
    loading: false,
    error: "",
    connected: false,
  }),
  actions: {
    async refresh() {
      this.loading = true;
      this.error = "";
      try {
        const [dashboard, jobs, nodes] = await Promise.all([
          api.dashboard(),
          api.jobs(),
          api.nodes(),
        ]);
        this.dashboard = dashboard;
        this.jobs = jobs;
        this.nodes = nodes;
        this.connected = true;
      } catch (error) {
        this.error = error instanceof Error ? error.message : "加载失败";
        this.connected = false;
      } finally {
        this.loading = false;
      }
    },
  },
});
