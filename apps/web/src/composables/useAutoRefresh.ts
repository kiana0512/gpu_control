import { onBeforeUnmount, onMounted, ref } from "vue";

export function useAutoRefresh(
  refresh: () => Promise<void>,
  intervalMs = 10_000,
) {
  const lastUpdatedAt = ref<Date | null>(null);
  const refreshing = ref(false);
  let timer: number | undefined;
  let activeRuns = 0;
  let latestRun = 0;

  async function performRun(force: boolean) {
    if (refreshing.value && !force) return;
    const runId = ++latestRun;
    activeRuns += 1;
    refreshing.value = true;
    try {
      await refresh();
      // A forced refresh is used when an operator changes the production/test
      // scope. Do not let an older request claim it produced the displayed
      // snapshot once a newer one has started.
      if (runId === latestRun) lastUpdatedAt.value = new Date();
    } catch {
      // The page owns and renders its request error. Keep polling so a
      // transient API restart heals without an F5 or an unhandled rejection.
    } finally {
      activeRuns -= 1;
      if (!activeRuns) refreshing.value = false;
    }
  }

  function run() {
    return performRun(false);
  }

  function forceRun() {
    return performRun(true);
  }

  function onVisible() {
    if (document.visibilityState === "visible") void run();
  }

  onMounted(() => {
    void run();
    timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void run();
    }, intervalMs);
    window.addEventListener("focus", run);
    document.addEventListener("visibilitychange", onVisible);
  });

  onBeforeUnmount(() => {
    if (timer) window.clearInterval(timer);
    window.removeEventListener("focus", run);
    document.removeEventListener("visibilitychange", onVisible);
  });

  return { run, forceRun, refreshing, lastUpdatedAt };
}
