import { onBeforeUnmount, onMounted, ref } from "vue";

export function useAutoRefresh(
  refresh: () => Promise<void>,
  intervalMs = 10_000,
) {
  const lastUpdatedAt = ref<Date | null>(null);
  const refreshing = ref(false);
  let timer: number | undefined;

  async function run() {
    if (refreshing.value) return;
    refreshing.value = true;
    try {
      await refresh();
      lastUpdatedAt.value = new Date();
    } catch {
      // The page owns and renders its request error. Keep polling so a
      // transient API restart heals without an F5 or an unhandled rejection.
    } finally {
      refreshing.value = false;
    }
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

  return { run, refreshing, lastUpdatedAt };
}
