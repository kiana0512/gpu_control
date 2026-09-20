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
    clientKind: "production" as "production" | "test" | "all",
  }),
  actions: {
    async refresh(clientKind?: "production" | "test" | "all") {
      const scope = clientKind ?? this.clientKind;
      this.clientKind = scope;
      this.loading = true;
      this.error = "";
      try {
        const results = await Promise.allSettled([
          api.dashboard(scope),
          // The overview renders only recent rows; do not pull the full
          // 500-row operations history on every ten-second dashboard tick.
          api.jobs(undefined, scope, 60),
          api.nodes(),
        ]);
        const [dashboard, jobs, nodes] = results;
        if (dashboard.status === "fulfilled") this.dashboard = dashboard.value;
        if (jobs.status === "fulfilled") this.jobs = jobs.value;
        if (nodes.status === "fulfilled") this.nodes = nodes.value;
        this.connected =
          dashboard.status === "fulfilled" || nodes.status === "fulfilled";
        const names = ["总览", "任务", "节点"] as const;
        const failures: string[] = [];
        results.forEach((result, index) => {
          if (result.status !== "rejected") return;
          failures.push(
            `${names[index] ?? "数据"}：${
              result.reason instanceof Error
                ? result.reason.message
                : "加载失败"
            }`,
          );
        });
        this.error = failures.join("；");
      } catch (error) {
        this.error = error instanceof Error ? error.message : "加载失败";
        this.connected = false;
      } finally {
        this.loading = false;
      }
    },
  },
});
