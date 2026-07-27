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

type WorkflowRow = {
  workflow_key: string;
  version: string;
  enabled: boolean;
  min_vram_mb: number;
  timeout_seconds: number;
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
const workflows = ref<WorkflowRow[]>([]);
const error = ref("");
const saving = ref(false);

const changed = computed(
  () => JSON.stringify(form) !== JSON.stringify(saved.value),
);
const orderedNodes = computed(() =>
  [...nodes.value].sort((left, right) => {
    const order = ["control-4090", "worker-3090-a", "worker-3090-b"];
    return order.indexOf(left.id) - order.indexOf(right.id);
  }),
);
const activeNodes = computed(() =>
  orderedNodes.value.filter(
    (node) => node.health === "ONLINE" && node.mode === "ACTIVE",
  ),
);
const totalSlots = computed(() =>
  nodes.value.reduce((total, node) => total + node.max_concurrency, 0),
);
const activeSlots = computed(() =>
  activeNodes.value.reduce((total, node) => total + node.max_concurrency, 0),
);
const usedSlots = computed(() =>
  activeNodes.value.reduce((total, node) => total + node.current_jobs, 0),
);
const enabledWorkflows = computed(() =>
  workflows.value.filter((workflow) => workflow.enabled),
);
const clusterMode = computed(() => {
  if (!nodes.value.length) return "等待节点状态";
  if (activeNodes.value.length === nodes.value.length)
    return `三节点并行（${activeSlots.value} 个执行槽位）`;
  return `降级运行（${activeNodes.value.length}/${nodes.value.length} 节点可接单）`;
});
const freeVramGb = computed({
  get: () => Number((form.overflow_4090_min_free_vram_mb / 1024).toFixed(1)),
  set: (value: number) => {
    form.overflow_4090_min_free_vram_mb = Math.round(Number(value || 0) * 1024);
  },
});

async function load() {
  error.value = "";
  try {
    const [settings, nodeRows, workflowRows] = await Promise.all([
      api.settings(),
      api.nodes(),
      api.workflows(),
    ]);
    Object.assign(form, settings);
    saved.value = { ...form };
    nodes.value = nodeRows;
    workflows.value = workflowRows as unknown as WorkflowRow[];
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

function nodeState(node: NodeInfo) {
  if (node.health !== "ONLINE")
    return node.health === "DEGRADED" ? "异常" : "离线";
  if (node.mode === "ACTIVE") return node.current_jobs ? "执行中" : "可接单";
  if (node.mode === "DRAINING") return "排空中";
  if (node.mode === "RESERVED") return "已暂停";
  if (node.mode === "OVERFLOW") return "备用";
  return "已停用";
}

function workflowName(key: string) {
  if (key === "imageclip-rgba") return "ImageClip 抠图";
  if (key === "modelview-inpaint") return "ModelView 局部重绘";
  return key;
}

const { run, refreshing, lastUpdatedAt } = useAutoRefresh(load);
</script>

<template>
  <div class="page scheduling-page">
    <div class="page-heading">
      <div>
        <h1>调度策略</h1>
        <p>三节点任务分配、真实业务优先级与 4090 安全回退</p>
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
        <span>当前集群模式</span><strong>{{ clusterMode }}</strong>
        <p>
          4090、3090-A、3090-B 独立握手并共同接单；每张 GPU 同时执行 1 个任务。
        </p>
      </div>
      <div class="mode-node">
        <span>实时执行槽位</span>
        <strong>{{ usedSlots }} / {{ activeSlots || totalSlots }}</strong>
        <small>{{ enabledWorkflows.length }} 个工作流版本已启用</small>
      </div>
    </section>

    <div class="policy-layout">
      <section class="policy-section cluster-section">
        <header>
          <div>
            <h2>实时计算节点</h2>
            <p>节点状态来自心跳；IP 变化后由节点身份与注册地址恢复连接。</p>
          </div>
          <span
            class="cluster-health"
            :class="{ degraded: activeNodes.length !== nodes.length }"
          >
            {{ activeNodes.length }} / {{ nodes.length }} 在线并接单
          </span>
        </header>
        <div class="scheduler-node-grid">
          <article
            v-for="node in orderedNodes"
            :key="node.id"
            class="scheduler-node"
          >
            <div>
              <i :class="node.health.toLowerCase()"></i>
              <strong>{{ node.display_name }}</strong>
              <span>{{ node.id }}</span>
            </div>
            <dl>
              <div>
                <dt>状态</dt>
                <dd>{{ nodeState(node) }}</dd>
              </div>
              <div>
                <dt>执行槽位</dt>
                <dd>{{ node.current_jobs }} / {{ node.max_concurrency }}</dd>
              </div>
              <div>
                <dt>可用显存</dt>
                <dd>{{ (node.free_vram_mb / 1024).toFixed(1) }} GB</dd>
              </div>
            </dl>
          </article>
        </div>
      </section>

      <section class="policy-section scheduler-rules-section">
        <header>
          <div>
            <h2>当前任务分配规则</h2>
            <p>以下规则由调度器实时执行，不依赖浏览器保持打开。</p>
          </div>
          <span class="rules-live">已生效</span>
        </header>
        <ol class="scheduler-rules">
          <li>
            <b>1</b>
            <div>
              <strong>兼容性先筛选</strong
              ><span
                >只允许工作流版本、模型、节点插件和显存均满足要求的节点领取任务。</span
              >
            </div>
          </li>
          <li>
            <b>2</b>
            <div>
              <strong>真实业务绝对优先</strong
              ><span
                >压力测试任务只占用真实客户暂时用不到的空闲
                GPU，不会抢占真实请求。</span
              >
            </div>
          </li>
          <li>
            <b>3</b>
            <div>
              <strong>客户公平与优先级老化</strong
              ><span
                >同优先级按客户轮转；等待时间会逐步提升有效优先级，避免单个客户长期饥饿。</span
              >
            </div>
          </li>
          <li>
            <b>4</b>
            <div>
              <strong>并发领取与防重复</strong
              ><span
                >三个节点并行领取兼容任务，数据库行锁与租约保证一个任务只会被一个节点执行。</span
              >
            </div>
          </li>
          <li>
            <b>5</b>
            <div>
              <strong>可追溯排队反馈</strong
              ><span
                >提交后返回任务
                ID、排队位置和状态地址；控制台分别展示真实任务与压力测试。</span
              >
            </div>
          </li>
          <li>
            <b>6</b>
            <div>
              <strong>失败恢复</strong
              ><span
                >节点失联或执行失败按策略重试，输入、输出、执行节点、耗时和错误均保留审计记录。</span
              >
            </div>
          </li>
        </ol>
        <div class="workflow-strip">
          <span>已启用工作流</span>
          <strong
            v-for="workflow in enabledWorkflows"
            :key="`${workflow.workflow_key}:${workflow.version}`"
          >
            {{ workflowName(workflow.workflow_key) }} · {{ workflow.version }}
          </strong>
        </div>
      </section>

      <section class="policy-section">
        <header>
          <div>
            <h2>4090 备用模式高级条件</h2>
            <p>
              当前 4090 为 ACTIVE 时不使用这些门槛；仅在维护时切换为 OVERFLOW
              后生效。
            </p>
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
            <small>备用模式下，队列达到这个数量时允许 4090 接单。</small></label
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
            <h2>4090 备用模式安全限制</h2>
            <p>防止 4090 被临时人工使用时，备用调度误占 GPU。</p>
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
