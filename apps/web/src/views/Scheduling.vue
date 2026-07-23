<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "../api";
import type { NodeInfo } from "../types";
import { useAutoRefresh } from "../composables/useAutoRefresh";

type SchedulingForm = {
  overflow_4090_auto_enabled: boolean;
  overflow_queue_threshold: number;
  overflow_wait_threshold_seconds: number;
  overflow_4090_min_free_vram_mb: number;
  overflow_4090_max_gpu_util_percent: number;
  overflow_4090_allowed_windows: string;
};

const form = reactive<SchedulingForm>({
  overflow_4090_auto_enabled: false,
  overflow_queue_threshold: 20,
  overflow_wait_threshold_seconds: 120,
  overflow_4090_min_free_vram_mb: 20000,
  overflow_4090_max_gpu_util_percent: 20,
  overflow_4090_allowed_windows: "",
});
const saved = ref<SchedulingForm>({ ...form });
const nodes = ref<NodeInfo[]>([]);
const error = ref("");
const saving = ref(false);

const controlNode = computed(() =>
  nodes.value.find((node) => node.id === "control-4090"),
);
const changed = computed(
  () => JSON.stringify(form) !== JSON.stringify(saved.value),
);
const directMode = computed(() => controlNode.value?.mode === "ACTIVE");
const freeVramGb = computed({
  get: () => Number((form.overflow_4090_min_free_vram_mb / 1024).toFixed(1)),
  set: (value: number) => {
    form.overflow_4090_min_free_vram_mb = Math.round(Number(value || 0) * 1024);
  },
});

async function load() {
  error.value = "";
  try {
    const [settings, nodeRows] = await Promise.all([
      api.settings(),
      api.nodes(),
    ]);
    Object.assign(form, settings);
    saved.value = { ...form };
    nodes.value = nodeRows;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "调度策略加载失败";
    throw cause;
  }
}

async function save() {
  if (!changed.value) return;
  await ElMessageBox.confirm(
    "确认保存调度策略吗？新任务会立即使用这些条件，正在运行的任务不受影响。",
    "保存调度策略",
    { type: "warning" },
  );
  saving.value = true;
  try {
    const keys = Object.keys(form) as (keyof SchedulingForm)[];
    for (const key of keys) {
      if (form[key] !== saved.value[key])
        await api.updateSetting(key, form[key]);
    }
    saved.value = { ...form };
    ElMessage.success("调度策略已保存");
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : "保存失败");
  } finally {
    saving.value = false;
  }
}

function reset() {
  Object.assign(form, saved.value);
}

const { run, refreshing, lastUpdatedAt } = useAutoRefresh(load);
</script>

<template>
  <div class="page scheduling-page">
    <div class="page-heading">
      <div>
        <h1>调度策略</h1>
        <p>决定任务什么时候使用 4090 备用算力</p>
      </div>
      <div class="heading-actions">
        <span class="refresh-state"
          ><i :class="{ spinning: refreshing }"></i>自动刷新 · 10 秒<br /><small
            >最后更新
            {{
              lastUpdatedAt?.toLocaleTimeString("zh-CN", { hour12: false }) ??
              "等待首次同步"
            }}</small
          ></span
        >
        <button class="secondary" @click="run">立即刷新</button>
      </div>
    </div>

    <div v-if="error" class="error-banner persistent-error">
      <strong>策略加载失败</strong><span>{{ error }}</span
      ><button @click="run">重试</button>
    </div>

    <section class="mode-summary">
      <div>
        <span>当前运行模式</span
        ><strong>{{
          directMode ? "单机模式（1×4090）" : "备用算力模式"
        }}</strong>
        <p>
          {{
            directMode
              ? "4090 已直接投入使用，用户任务无需等待溢出条件。"
              : "主算力繁忙且满足下方条件时，4090 才会接单。"
          }}
        </p>
      </div>
      <div class="mode-node">
        <span>4090 状态</span
        ><strong
          >{{ controlNode?.health === "ONLINE" ? "在线" : "离线" }} ·
          {{
            controlNode?.mode === "ACTIVE"
              ? "正在接单"
              : controlNode?.mode === "RESERVED"
                ? "已暂停"
                : "备用"
          }}</strong
        >
      </div>
    </section>

    <div class="policy-layout">
      <section class="policy-section">
        <header>
          <div>
            <h2>自动溢出</h2>
            <p>仅当 4090 被设置为“备用算力”时生效。</p>
          </div>
          <label class="switch-control"
            ><input
              v-model="form.overflow_4090_auto_enabled"
              type="checkbox"
            /><span></span
            >{{ form.overflow_4090_auto_enabled ? "已开启" : "已关闭" }}</label
          >
        </header>
        <div class="policy-fields">
          <label
            ><span>排队任务达到</span>
            <div>
              <input
                v-model.number="form.overflow_queue_threshold"
                type="number"
                min="1"
              /><b>个</b>
            </div>
            <small>队列达到这个数量时允许启用 4090。</small></label
          >
          <label
            ><span>最长等待超过</span>
            <div>
              <input
                v-model.number="form.overflow_wait_threshold_seconds"
                type="number"
                min="1"
              /><b>秒</b>
            </div>
            <small>即使队列不长，等待过久也允许启用。</small></label
          >
          <label
            ><span>允许使用时间</span>
            <div>
              <input
                v-model.trim="form.overflow_4090_allowed_windows"
                placeholder="留空表示全天；例如 22:00-06:00"
              />
            </div>
            <small>多个时间段用英文逗号分隔。</small></label
          >
        </div>
      </section>

      <section class="policy-section">
        <header>
          <div>
            <h2>安全限制</h2>
            <p>防止 4090 正在被人工使用时被调度器抢占。</p>
          </div>
        </header>
        <div class="policy-fields two-columns">
          <label
            ><span>至少保留空闲显存</span>
            <div>
              <input
                v-model.number="freeVramGb"
                type="number"
                min="0"
                step="0.5"
              /><b>GB</b>
            </div>
            <small>低于该值时不会自动向 4090 分配任务。</small></label
          >
          <label
            ><span>GPU 利用率低于</span>
            <div>
              <input
                v-model.number="form.overflow_4090_max_gpu_util_percent"
                type="number"
                min="0"
                max="100"
              /><b>%</b>
            </div>
            <small>高于该值表示 GPU 正忙，不会自动接单。</small></label
          >
        </div>
      </section>
    </div>

    <div class="save-bar" :class="{ visible: changed }">
      <span>{{ changed ? "有尚未保存的修改" : "所有设置已保存" }}</span>
      <div>
        <button class="secondary" :disabled="!changed || saving" @click="reset">
          撤销修改</button
        ><button class="primary" :disabled="!changed || saving" @click="save">
          {{ saving ? "保存中…" : "保存策略" }}
        </button>
      </div>
    </div>
  </div>
</template>
