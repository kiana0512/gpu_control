import { createRouter, createWebHistory } from "vue-router";
import { session } from "./api";

const Dashboard = () => import("./views/Dashboard.vue");
const Jobs = () => import("./views/Jobs.vue");
const Analysis = () => import("./views/Analysis.vue");
const Nodes = () => import("./views/Nodes.vue");
const CloudServers = () => import("./views/CloudServers.vue");
const Assets = () => import("./views/Assets.vue");
const Realesrgan = () => import("./views/Realesrgan.vue");
const CodexRuntime = () => import("./views/CodexRuntime.vue");
const ResourceList = () => import("./views/ResourceList.vue");
const Scheduling = () => import("./views/Scheduling.vue");
const Logs = () => import("./views/Logs.vue");
const SystemInfo = () => import("./views/SystemInfo.vue");
const Login = () => import("./views/Login.vue");

export const router = createRouter({
  history: createWebHistory(),
  scrollBehavior(to, from, savedPosition) {
    if (savedPosition) return savedPosition;
    if (to.path !== from.path) return { left: 0, top: 0 };
    return false;
  },
  routes: [
    { path: "/login", component: Login, meta: { public: true } },
    { path: "/", component: Dashboard },
    { path: "/jobs", component: Jobs },
    { path: "/analysis", component: Analysis },
    { path: "/nodes", component: Nodes },
    { path: "/cloud-servers", component: CloudServers },
    { path: "/asset-processing", component: Assets },
    { path: "/realesrgan", component: Realesrgan },
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
