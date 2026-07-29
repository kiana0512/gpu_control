import { createRouter, createWebHistory } from "vue-router";
import { session } from "./api";

const Dashboard = () => import("./views/Dashboard.vue");
const Jobs = () => import("./views/Jobs.vue");
const Nodes = () => import("./views/Nodes.vue");
const Assets = () => import("./views/Assets.vue");
const CodexRuntime = () => import("./views/CodexRuntime.vue");
const ResourceList = () => import("./views/ResourceList.vue");
const Scheduling = () => import("./views/Scheduling.vue");
const Logs = () => import("./views/Logs.vue");
const SystemInfo = () => import("./views/SystemInfo.vue");
const Login = () => import("./views/Login.vue");

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", component: Login, meta: { public: true } },
    { path: "/", component: Dashboard },
    { path: "/jobs", component: Jobs },
    { path: "/nodes", component: Nodes },
    { path: "/asset-processing", component: Assets },
    { path: "/codex", component: CodexRuntime },
    {
      path: "/workflows",
      component: ResourceList,
      props: { title: "工作流管理", kind: "workflows" },
    },
    {
      path: "/clients",
      component: ResourceList,
      props: { title: "API 客户", kind: "clients" },
    },
    {
      path: "/scheduling",
      component: Scheduling,
    },
    {
      path: "/alerts",
      component: ResourceList,
      props: { title: "告警与飞书", kind: "alerts" },
    },
    {
      path: "/audit",
      component: ResourceList,
      props: { title: "审计日志", kind: "audit" },
    },
    {
      path: "/logs",
      component: Logs,
    },
    {
      path: "/settings",
      component: SystemInfo,
    },
  ],
});
router.beforeEach((to) =>
  !to.meta.public && !session.get() ? "/login" : true,
);
