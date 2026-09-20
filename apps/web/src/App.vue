<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  type Component,
  watch,
} from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  Bell,
  Box,
  Clock,
  Cloudy,
  Connection,
  Cpu,
  DataAnalysis,
  Document,
  Key,
  List,
  Operation,
  SetUp,
  Setting,
  SwitchButton,
  TrendCharts,
} from "@element-plus/icons-vue";
import { session } from "./api";

const route = useRoute();
const router = useRouter();
const buildVersion = import.meta.env.VITE_GPU_CONTROL_VERSION || "development";
const buildRevision = (
  import.meta.env.VITE_GPU_CONTROL_REVISION || "unknown"
).slice(0, 12);

type MenuItem = {
  path: string;
  label: string;
  description: string;
  keywords: string;
  icon: Component;
};
type MenuGroup = { label: string; index: string; items: MenuItem[] };
type FocusTarget = { focus: () => void };
type ScrollTarget = {
  scrollTo: (options: { top: number; left: number; behavior: "auto" }) => void;
};

const menuGroups: MenuGroup[] = [
  {
    label: "运行态势",
    index: "01",
    items: [
      {
        path: "/",
        label: "控制总览",
        description: "队列、容量、节点与风险的实时运行视图",
        keywords: "dashboard overview 总览 首页",
        icon: Cpu,
      },
      {
        path: "/jobs",
        label: "任务运营",
        description: "检索、跟踪并处置全部 GPU 任务",
        keywords: "job task queue 任务 队列",
        icon: List,
      },
      {
        path: "/analysis",
        label: "性能洞察",
        description: "端到端延迟、阶段耗时与吞吐分析",
        keywords: "performance latency 性能 延迟",
        icon: TrendCharts,
      },
    ],
  },
  {
    label: "算力与执行",
    index: "02",
    items: [
      {
        path: "/nodes",
        label: "GPU 节点",
        description: "物理节点状态、槽位和工作流兼容性",
        keywords: "gpu node 节点 显卡",
        icon: Box,
      },
      {
        path: "/cloud-servers",
        label: "云算力",
        description: "AutoDL 实例、电源、SSH 与定时策略",
        keywords: "autodl cloud pro6000 云 GPU 开机 关机 ssh",
        icon: Cloudy,
      },
      {
        path: "/asset-processing",
        label: "资产流水线",
        description: "UV、拓扑与烘焙任务运行状态",
        keywords: "asset uv retopology bake 资产",
        icon: SetUp,
      },
      {
        path: "/realesrgan",
        label: "AI 高清化",
        description: "Real-ESRGAN 服务容量与任务观测",
        keywords: "realesrgan upscale 高清化",
        icon: Cpu,
      },
      {
        path: "/codex",
        label: "Codex 运行时",
        description: "节点认证、真实探针与执行上下文",
        keywords: "codex worker agent 探针",
        icon: Connection,
      },
    ],
  },
  {
    label: "接入与策略",
    index: "03",
    items: [
      {
        path: "/workflows",
        label: "工作流注册表",
        description: "版本、兼容性与启用状态",
        keywords: "workflow 工作流 版本",
        icon: DataAnalysis,
      },
      {
        path: "/clients",
        label: "API 客户",
        description: "访问密钥、配额和网络策略",
        keywords: "api client key 客户 密钥",
        icon: Key,
      },
      {
        path: "/scheduling",
        label: "调度策略",
        description: "优先级、路由和容量治理",
        keywords: "schedule route 调度 路由",
        icon: Operation,
      },
    ],
  },
  {
    label: "可靠性",
    index: "04",
    items: [
      {
        path: "/alerts",
        label: "告警中心",
        description: "活动风险和通知投递状态",
        keywords: "alert warning 告警",
        icon: Bell,
      },
      {
        path: "/audit",
        label: "操作审计",
        description: "管理操作与变更记录",
        keywords: "audit log 审计",
        icon: Document,
      },
      {
        path: "/logs",
        label: "运行日志",
        description: "跨服务检索诊断信息",
        keywords: "logs 日志 debug",
        icon: Clock,
      },
      {
        path: "/settings",
        label: "系统信息",
        description: "版本、端点与环境状态",
        keywords: "system settings version 系统",
        icon: Setting,
      },
    ],
  },
];

const menu = menuGroups.flatMap((group) => group.items);
const showShell = computed(() => route.path !== "/login");
const operatorRole = computed(() => session.role() ?? "unknown");
const operatorLabel = computed(() => {
  if (operatorRole.value === "admin") return "系统管理员";
  if (operatorRole.value === "operator") return "运行操作员";
  if (operatorRole.value === "viewer") return "只读观察员";
  return "当前用户";
});
const currentItem = computed(
  () => menu.find((item) => item.path === route.path) ?? menu[0],
);
const currentGroup = computed(
  () =>
    menuGroups.find((group) =>
      group.items.some((item) => item.path === route.path),
    ) ?? menuGroups[0],
);
const navCollapsed = ref(localStorage.getItem("gpu-control-nav") === "compact");
const mobileNavOpen = ref(false);
const mainContent = ref<ScrollTarget | null>(null);
const commandOpen = ref(false);
const commandQuery = ref("");
const commandIndex = ref(0);
const commandInput = ref<HTMLInputElement | null>(null);
const commandDialog = ref<FocusTarget | null>(null);
let commandReturnFocus: FocusTarget | null = null;
const normalizedQuery = computed(() => commandQuery.value.trim().toLowerCase());
const commandResults = computed(() => {
  const query = normalizedQuery.value;
  if (!query) return menu;
  return menu.filter((item) =>
    `${item.label} ${item.description} ${item.keywords}`
      .toLowerCase()
      .includes(query),
  );
});

function toggleNavigation() {
  navCollapsed.value = !navCollapsed.value;
  localStorage.setItem(
    "gpu-control-nav",
    navCollapsed.value ? "compact" : "expanded",
  );
}

function closeNavigationMenu(event?: globalThis.MouseEvent) {
  (event?.currentTarget as { blur?: () => void } | null)?.blur?.();
}

function focusTarget(value: unknown): FocusTarget | null {
  return value && typeof (value as FocusTarget).focus === "function"
    ? (value as FocusTarget)
    : null;
}

function openCommand(event?: globalThis.MouseEvent) {
  commandReturnFocus =
    focusTarget(event?.currentTarget) ?? focusTarget(document.activeElement);
  commandQuery.value = "";
  commandIndex.value = 0;
  commandOpen.value = true;
  window.requestAnimationFrame(() => {
    commandInput.value?.focus();
  });
}

function closeCommand(restoreFocus = true) {
  commandOpen.value = false;
  const returnFocus = commandReturnFocus;
  commandReturnFocus = null;
  if (restoreFocus && returnFocus) void nextTick(() => returnFocus.focus());
}

async function navigate(item: MenuItem) {
  closeCommand(false);
  mobileNavOpen.value = false;
  await router.push(item.path);
}

function onGlobalKeydown(event: globalThis.KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    if (commandOpen.value) closeCommand();
    else openCommand();
    return;
  }
  if (!commandOpen.value) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeCommand();
    return;
  }
  if (event.key === "ArrowDown") {
    event.preventDefault();
    commandIndex.value = Math.min(
      commandIndex.value + 1,
      Math.max(0, commandResults.value.length - 1),
    );
  }
  if (event.key === "ArrowUp") {
    event.preventDefault();
    commandIndex.value = Math.max(0, commandIndex.value - 1);
  }
  if (event.key === "Enter") {
    const item = commandResults.value[commandIndex.value];
    if (item) void navigate(item);
  }
}

function logout() {
  session.clear();
  void router.push("/login");
}

watch(commandResults, () => {
  commandIndex.value = 0;
});
watch(
  () => route.path,
  async () => {
    mobileNavOpen.value = false;
    closeCommand(false);
    await nextTick();
    mainContent.value?.scrollTo({ top: 0, left: 0, behavior: "auto" });
  },
);
onMounted(() => window.addEventListener("keydown", onGlobalKeydown));
onBeforeUnmount(() => window.removeEventListener("keydown", onGlobalKeydown));
</script>

<template>
  <router-view v-if="!showShell" />
  <div
    v-else
    class="control-shell"
    :class="{ 'nav-compact': navCollapsed, 'mobile-nav-open': mobileNavOpen }"
  >
    <button
      v-if="mobileNavOpen"
      class="mobile-nav-scrim"
      aria-label="关闭导航"
      @click="mobileNavOpen = false"
    ></button>
    <aside class="control-nav">
      <div class="control-brand">
        <span class="control-brand-mark">GC</span>
        <div class="control-brand-copy">
          <strong>GPU CONTROL</strong>
          <small>OPERATIONS SYSTEM</small>
        </div>
        <button
          type="button"
          class="nav-collapse"
          :aria-label="navCollapsed ? '展开导航' : '收起导航'"
          @click="toggleNavigation"
        >
          <span>{{ navCollapsed ? "›" : "‹" }}</span>
        </button>
      </div>

      <button
        type="button"
        class="nav-command"
        aria-haspopup="dialog"
        :aria-expanded="commandOpen"
        @click="openCommand($event)"
      >
        <span class="nav-command-icon">⌕</span>
        <span class="nav-command-copy">查找功能或页面</span>
        <kbd>⌘ K</kbd>
      </button>

      <nav aria-label="控制中心导航">
        <section
          v-for="group in menuGroups"
          :key="group.index"
          class="control-nav-group"
          :class="{ 'nav-group-current': currentGroup.index === group.index }"
          :aria-label="`${group.label}导航`"
        >
          <header>
            <router-link :to="group.items[0].path">
              <span>{{ group.index }}</span><strong>{{ group.label }}</strong>
            </router-link>
          </header>
          <router-link
            v-for="item in group.items"
            :key="item.path"
            :to="item.path"
            :title="navCollapsed ? item.label : undefined"
            @click="closeNavigationMenu($event)"
          >
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.label }}</span>
            <i></i>
          </router-link>
        </section>
      </nav>

      <footer class="control-nav-footer">
        <div class="control-plane-pulse">
          <i></i><span>CONTROL PLANE</span><b>ONLINE</b>
        </div>
        <small>v{{ buildVersion }} · {{ buildRevision }}</small>
      </footer>
    </aside>

    <section class="control-stage">
      <header class="control-command-bar">
        <button
          type="button"
          class="mobile-nav-trigger"
          aria-label="打开导航"
          @click="mobileNavOpen = true"
        >
          ☰
        </button>
        <div class="route-context">
          <span>GPU CONTROL / {{ currentItem.label }}</span>
          <strong>{{ currentItem.description }}</strong>
        </div>
        <nav class="route-shortcuts" :aria-label="`${currentGroup.label}页面`">
          <router-link
            v-for="item in currentGroup.items"
            :key="item.path"
            :to="item.path"
          >
            {{ item.label }}
          </router-link>
        </nav>
        <div class="command-status">
          <span class="live-status"><i></i>生产环境</span>
          <button type="button" class="operator-menu" @click="logout">
            <span class="operator-avatar">管</span>
            <span class="operator-copy"
              ><b>{{ operatorLabel }}</b
              ><small>{{ operatorRole }} · 退出登录</small></span
            >
            <el-icon><SwitchButton /></el-icon>
          </button>
        </div>
      </header>
      <main id="main-content" ref="mainContent">
        <router-view v-slot="{ Component: RouteComponent }">
          <transition name="control-page" mode="out-in">
            <component :is="RouteComponent" :key="route.path" />
          </transition>
        </router-view>
      </main>
    </section>

    <Teleport to="body">
      <div
        v-if="commandOpen"
        class="command-backdrop"
        @mousedown.self="closeCommand()"
      >
        <section
          ref="commandDialog"
          class="command-palette"
          role="dialog"
          aria-modal="true"
          aria-labelledby="command-palette-title"
          tabindex="-1"
        >
          <header>
            <span id="command-palette-title">⌕</span>
            <input
              ref="commandInput"
              v-model="commandQuery"
              aria-label="搜索页面"
              placeholder="输入页面、能力或操作，例如云 GPU、任务、SSH…"
            />
            <kbd>ESC</kbd>
          </header>
          <div class="command-result-caption">
            <span>导航到</span><small>{{ commandResults.length }} 个结果</small>
          </div>
          <div class="command-results" role="listbox" aria-label="页面搜索结果">
            <button
              v-for="(item, index) in commandResults"
              :key="item.path"
              type="button"
              role="option"
              :class="{ active: commandIndex === index }"
              :aria-selected="commandIndex === index"
              @mouseenter="commandIndex = index"
              @click="navigate(item)"
            >
              <el-icon><component :is="item.icon" /></el-icon>
              <span
                ><strong>{{ item.label }}</strong
                ><small>{{ item.description }}</small></span
              >
              <b>↵</b>
            </button>
            <div v-if="!commandResults.length" class="command-empty">
              没有匹配的控制页面
            </div>
          </div>
          <footer>
            <span>↑↓ 选择</span><span>Enter 前往</span><span>Esc 关闭</span>
          </footer>
        </section>
      </div>
    </Teleport>
  </div>
</template>
